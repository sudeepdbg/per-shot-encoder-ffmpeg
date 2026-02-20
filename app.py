import os
import subprocess
import uuid
import time
import threading
import re
import traceback
from flask import Flask, request, render_template, jsonify, send_file, after_this_request
from werkzeug.utils import secure_filename
from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import ContentDetector

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'
app.config['SCENE_FOLDER'] = 'scenes'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB limit (PythonAnywhere free tier)

for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER'], app.config['SCENE_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def detect_scenes(video_path, threshold=30.0):
    video_manager = VideoManager([video_path])
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))
    video_manager.set_downscale_factor()
    video_manager.start()
    scene_manager.detect_scenes(frame_source=video_manager)
    scene_list = scene_manager.get_scene_list()
    video_manager.release()
    return [(start.get_seconds(), end.get_seconds()) for start, end in scene_list]

def encode_scene(input_path, output_path, start, end, base_crf, preset):
    """Encode a single scene with robust seeking and error fallback."""
    if end is None:
        # No end time – encode until the end of the video
        print(f"Scene {start:.1f}-end (full remainder) using base CRF {base_crf}")
        crf = base_crf
        cmd = [
            'ffmpeg', '-y', '-i', input_path, '-ss', str(start),
            '-c:v', 'libx264', '-crf', str(crf), '-preset', preset,
            '-c:a', 'aac', '-avoid_negative_ts', 'make_zero',
            '-fflags', '+genpts', output_path
        ]
    else:
        duration = end - start
        # Adjust CRF based on duration (simple heuristic)
        if duration > 10:
            crf = base_crf + 4
        elif duration > 5:
            crf = base_crf + 2
        else:
            crf = base_crf - 2
        crf = max(18, min(35, crf))
        print(f"Scene {start:.1f}-{end:.1f} (dur={duration:.1f}s) using CRF {crf}")

        # Primary command: accurate seek (ss after -i) with duration
        cmd = [
            'ffmpeg', '-y', '-i', input_path, '-ss', str(start),
            '-t', str(duration), '-c:v', 'libx264', '-crf', str(crf),
            '-preset', preset, '-c:a', 'aac', '-avoid_negative_ts', 'make_zero',
            '-fflags', '+genpts', output_path
        ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"!!! FFmpeg error for scene {start}-{end} (accurate seek) !!!")
        print(f"Command: {' '.join(cmd)}")
        print(f"FFmpeg stderr:\n{e.stderr}")

        # Fallback: try fast seek (ss before -i) – may work where accurate seek fails
        print("Attempting fallback with fast seek...")
        if end is None:
            cmd_fallback = [
                'ffmpeg', '-y', '-ss', str(start), '-i', input_path,
                '-c:v', 'libx264', '-crf', str(crf), '-preset', preset,
                '-c:a', 'aac', '-avoid_negative_ts', 'make_zero',
                '-fflags', '+genpts', output_path
            ]
        else:
            cmd_fallback = [
                'ffmpeg', '-y', '-ss', str(start), '-i', input_path,
                '-t', str(duration), '-c:v', 'libx264', '-crf', str(crf),
                '-preset', preset, '-c:a', 'aac', '-avoid_negative_ts', 'make_zero',
                '-fflags', '+genpts', output_path
            ]
        try:
            subprocess.run(cmd_fallback, check=True, capture_output=True, text=True)
            print("Fallback succeeded.")
            return
        except subprocess.CalledProcessError as e2:
            print("!!! Fallback also failed !!!")
            print(f"Command: {' '.join(cmd_fallback)}")
            print(f"FFmpeg stderr:\n{e2.stderr}")
            raise  # Re-raise the original exception (or e2) to stop processing

def concatenate_scenes(scene_files, output_path):
    concat_list = os.path.join(app.config['SCENE_FOLDER'], 'concat_list.txt')
    with open(concat_list, 'w') as f:
        for sf in scene_files:
            f.write(f"file '{os.path.abspath(sf)}'\n")
    cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_list, '-c', 'copy', output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print("!!! Concatenation failed !!!")
        print(f"Command: {' '.join(cmd)}")
        print(f"FFmpeg stderr:\n{e.stderr}")
        raise

def calculate_metrics(original, compressed):
    """Calculate PSNR and SSIM with robust parsing."""
    psnr = ssim = None
    try:
        # PSNR
        cmd_psnr = ['ffmpeg', '-i', original, '-i', compressed, '-lavfi', 'psnr', '-f', 'null', '-']
        result = subprocess.run(cmd_psnr, capture_output=True, text=True, timeout=120)
        print("===== PSNR OUTPUT =====")
        print(result.stderr)
        for line in result.stderr.split('\n'):
            if 'PSNR' in line and 'average:' in line:
                match = re.search(r'average:([0-9.]+)', line)
                if match:
                    psnr = float(match.group(1))
                    break

        # SSIM
        cmd_ssim = ['ffmpeg', '-i', original, '-i', compressed, '-lavfi', 'ssim', '-f', 'null', '-']
        result = subprocess.run(cmd_ssim, capture_output=True, text=True, timeout=120)
        print("===== SSIM OUTPUT =====")
        print(result.stderr)
        for line in result.stderr.split('\n'):
            if 'SSIM' in line and 'All:' in line:
                match = re.search(r'All:([0-9.]+)', line)
                if match:
                    ssim = float(match.group(1))
                    break
    except Exception as e:
        print(f"Metric calculation error: {e}")
        traceback.print_exc()
    return psnr, ssim

def delayed_cleanup(file_path, delay=5):
    def delete():
        time.sleep(delay)
        try:
            os.remove(file_path)
            print(f"Cleaned up {file_path}")
        except:
            pass
    threading.Thread(target=delete).start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/encode', methods=['POST'])
def encode():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400

    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{uuid.uuid4()}_{filename}")
    file.save(input_path)

    crf = request.form.get('crf', '23')
    preset = request.form.get('preset', 'medium')
    resolution = request.form.get('resolution', '')
    use_per_shot = request.form.get('per_shot') == 'on'

    output_filename = f"compressed_{uuid.uuid4()}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    try:
        if use_per_shot:
            try:
                scenes = detect_scenes(input_path)
                if not scenes:
                    scenes = [(0, None)]

                scene_files = []
                for i, (start, end) in enumerate(scenes):
                    scene_out = os.path.join(app.config['SCENE_FOLDER'], f"scene_{i:03d}.mp4")
                    encode_scene(input_path, scene_out, start, end, int(crf), preset)
                    scene_files.append(scene_out)

                concatenate_scenes(scene_files, output_path)

                for sf in scene_files:
                    os.remove(sf)
            except Exception as e:
                print("!!! Per-shot encoding failed !!!")
                traceback.print_exc()
                raise
        else:
            cmd = ['ffmpeg', '-y', '-i', input_path, '-c:v', 'libx264',
                   '-crf', crf, '-preset', preset]
            if resolution:
                cmd.extend(['-vf', f'scale={resolution}'])
            cmd.extend(['-c:a', 'aac', output_path])
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                print("!!! Normal encoding failed !!!")
                print(f"Command: {' '.join(cmd)}")
                print(f"FFmpeg stderr:\n{e.stderr}")
                raise

        orig_size = os.path.getsize(input_path)
        comp_size = os.path.getsize(output_path)
        savings = (1 - comp_size / orig_size) * 100 if orig_size > 0 else 0

        psnr, ssim = calculate_metrics(input_path, output_path)

        download_url = f'/download/{output_filename}?input_id={os.path.basename(input_path)}&output_id={output_filename}'

        return jsonify({
            'success': True,
            'download_url': download_url,
            'original_size': orig_size,
            'compressed_size': comp_size,
            'savings_percent': round(savings, 2),
            'psnr': round(psnr, 2) if psnr else None,
            'ssim': round(ssim, 3) if ssim else None
        })

    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        print("!!! Unhandled exception in /encode !!!")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download(filename):
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    input_id = request.args.get('input_id')
    output_id = request.args.get('output_id')

    if not os.path.exists(file_path):
        return "File not found", 404

    @after_this_request
    def cleanup(response):
        if input_id:
            input_path = os.path.join(app.config['UPLOAD_FOLDER'], input_id)
            delayed_cleanup(input_path)
        if output_id:
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_id)
            delayed_cleanup(output_path)
        return response

    return send_file(file_path, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)

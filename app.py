import os
import tempfile
from flask import Flask, request, jsonify, render_template, send_file
import librosa
import numpy as np
from scipy import signal
import uuid

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB limit

# Temporary storage for uploaded files (in memory)
UPLOAD_FOLDER = tempfile.mkdtemp()
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'm4a', 'flac', 'aac'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def compute_offset(reference_path, test_path, sr=22050, hop_length=512, threshold_ms=50):
    """
    Returns offset in ms and a boolean indicating if manual review is needed.
    Positive offset means test is delayed relative to reference.
    """
    ref, _ = librosa.load(reference_path, sr=sr, mono=True)
    test, _ = librosa.load(test_path, sr=sr, mono=True)

    # Trim leading/trailing silence to avoid false offsets from different padding
    ref, _ = librosa.effects.trim(ref)
    test, _ = librosa.effects.trim(test)

    # Compute RMS energy envelope
    ref_rms = librosa.feature.rms(y=ref, hop_length=hop_length)[0]
    test_rms = librosa.feature.rms(y=test, hop_length=hop_length)[0]

    # Normalize RMS to [0,1] to reduce amplitude influence
    ref_rms = (ref_rms - ref_rms.min()) / (ref_rms.max() - ref_rms.min() + 1e-10)
    test_rms = (test_rms - test_rms.min()) / (test_rms.max() - test_rms.min() + 1e-10)

    # Cross-correlate
    correlation = signal.correlate(test_rms, ref_rms, mode='same')
    lag = np.argmax(correlation) - len(ref_rms)//2
    offset_ms = lag * hop_length / sr * 1000

    # Determine if manual validation is needed
    needs_review = abs(offset_ms) > threshold_ms
    return round(offset_ms, 2), needs_review

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files provided'}), 400

    files = request.files.getlist('files[]')
    if len(files) < 2:
        return jsonify({'error': 'Please upload at least two files'}), 400

    # Save uploaded files with unique names to avoid collisions
    saved_paths = []
    for file in files:
        if file and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            unique_name = f"{uuid.uuid4().hex}.{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, unique_name)
            file.save(save_path)
            saved_paths.append((file.filename, save_path))
        else:
            return jsonify({'error': f'File type not allowed: {file.filename}'}), 400

    # Use the first file as reference
    ref_filename, ref_path = saved_paths[0]
    results = []

    for filename, path in saved_paths[1:]:
        offset_ms, needs_review = compute_offset(ref_path, path)
        results.append({
            'filename': filename,
            'offset_ms': offset_ms,
            'needs_review': needs_review
        })

    # Clean up temporary files (optional – you can keep them for download)
    # We'll keep them for now; you may add a cleanup later.

    return jsonify({
        'reference': ref_filename,
        'results': results
    })

if __name__ == '__main__':
    app.run(debug=True)

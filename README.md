Proof of Concept (PoC) for Content-Adaptive Encoding (CAE). This tool leverages AI-driven scene detection to dynamically optimize FFmpeg encoding parameters on a per-shot basis, maximizing storage savings without compromising visual fidelity.

🚀 Live Demo
(Note: Public demo is capped at 50MB per upload)

📖 Overview
Standard video encoding typically applies a "one-size-fits-all" approach to an entire file. This project demonstrates an intelligent orchestration layer that treats every scene as a unique entity. By analyzing the complexity and duration of individual shots, the encoder "swings" the Constant Rate Factor (CRF) to allocate bits where they provide the highest visual ROI.

Key Features
Automated Scene Detection: Uses PySceneDetect to identify shot boundaries.

Dynamic CRF Heuristics: Automatically adjusts quality targets based on scene duration and action level.

Dual-Stage Seeking: Robust FFmpeg implementation using accurate and fast-seek fallbacks to handle varied container formats.

Quality Metrics: Integrated PSNR and SSIM calculation for objective performance benchmarking.

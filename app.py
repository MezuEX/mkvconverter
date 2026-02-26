import os
import subprocess
import threading
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from werkzeug.utils import secure_filename
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size

# Konfigurasi folder
UPLOAD_FOLDER = 'uploads'
CONVERTED_FOLDER = 'converted'
ALLOWED_EXTENSIONS = {'mkv'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CONVERTED_FOLDER'] = CONVERTED_FOLDER

# Buat folder jika belum ada
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CONVERTED_FOLDER, exist_ok=True)

# Status konversi
conversion_status = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_video_info(filepath):
    """Mendapatkan informasi video menggunakan ffprobe"""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        
        video_info = {
            'duration': float(info['format']['duration']),
            'size': int(info['format']['size']),
            'format': info['format']['format_name'],
            'video_streams': 0,
            'audio_streams': 0
        }
        
        for stream in info['streams']:
            if stream['codec_type'] == 'video':
                video_info['video_streams'] += 1
                video_info['video_codec'] = stream['codec_name']
                video_info['resolution'] = f"{stream.get('width', 'N/A')}x{stream.get('height', 'N/A')}"
            elif stream['codec_type'] == 'audio':
                video_info['audio_streams'] += 1
                video_info['audio_codec'] = stream['codec_name']
        
        return video_info
    except Exception as e:
        return None

def convert_video(input_path, output_path, quality, job_id):
    """Fungsi untuk konversi video dengan progress tracking"""
    try:
        conversion_status[job_id] = {
            'status': 'processing',
            'progress': 0,
            'message': 'Memulai konversi...'
        }
        
        # Quality presets
        quality_presets = {
            'low': {'crf': '28', 'preset': 'fast'},
            'medium': {'crf': '23', 'preset': 'medium'},
            'high': {'crf': '18', 'preset': 'slow'},
            'veryhigh': {'crf': '16', 'preset': 'veryslow'}
        }
        
        preset = quality_presets.get(quality, quality_presets['medium'])
        
        # FFmpeg command dengan progress tracking
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-c:v', 'libx264',
            '-preset', preset['preset'],
            '-crf', preset['crf'],
            '-c:a', 'aac',
            '-b:a', '192k',
            '-movflags', '+faststart',
            '-progress', 'pipe:1',
            '-y',  # Overwrite output file
            output_path
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )
        
        # Process output untuk progress tracking
        for line in process.stdout:
            if 'out_time=' in line:
                time_str = line.split('=')[1].strip()
                try:
                    # Convert time string to seconds
                    if '.' in time_str:
                        time_parts = time_str.split('.')
                        time_main = time_parts[0]
                        milliseconds = time_parts[1][:3] if len(time_parts) > 1 else '000'
                    else:
                        time_main = time_str
                        milliseconds = '000'
                    
                    # Parse time
                    time_obj = datetime.strptime(time_main, '%H:%M:%S')
                    total_seconds = time_obj.hour * 3600 + time_obj.minute * 60 + time_obj.second
                    total_seconds += int(milliseconds) / 1000
                    
                    # Get duration from input file
                    video_info = get_video_info(input_path)
                    if video_info and video_info['duration'] > 0:
                        progress = (total_seconds / video_info['duration']) * 100
                        conversion_status[job_id]['progress'] = min(progress, 100)
                        conversion_status[job_id]['message'] = f'Mengkonversi... {progress:.1f}%'
                        
                except Exception as e:
                    continue
            
            elif 'error' in line.lower():
                conversion_status[job_id]['message'] = f'Error: {line.strip()}'
        
        process.wait()
        
        if process.returncode == 0:
            conversion_status[job_id] = {
                'status': 'completed',
                'progress': 100,
                'message': 'Konversi berhasil!',
                'output_file': output_path
            }
            
            # Hapus file input setelah konversi berhasil
            try:
                os.remove(input_path)
            except:
                pass
                
        else:
            conversion_status[job_id] = {
                'status': 'error',
                'progress': 0,
                'message': 'Konversi gagal!'
            }
            
    except Exception as e:
        conversion_status[job_id] = {
            'status': 'error',
            'progress': 0,
            'message': f'Error: {str(e)}'
        }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('Tidak ada file yang dipilih', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    quality = request.form.get('quality', 'medium')
    
    if file.filename == '':
        flash('Tidak ada file yang dipilih', 'error')
        return redirect(url_for('index'))
    
    if file and allowed_file(file.filename):
        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        original_filename = secure_filename(file.filename)
        filename = f"{timestamp}_{original_filename}"
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Save uploaded file
        file.save(input_path)
        
        # Generate output filename
        output_filename = os.path.splitext(original_filename)[0] + '.mp4'
        output_path = os.path.join(app.config['CONVERTED_FOLDER'], output_filename)
        
        # Generate job ID
        job_id = timestamp
        
        # Start conversion in background thread
        thread = threading.Thread(
            target=convert_video,
            args=(input_path, output_path, quality, job_id)
        )
        thread.daemon = True
        thread.start()
        
        return redirect(url_for('conversion_status', job_id=job_id, output_filename=output_filename))
    
    else:
        flash('Format file tidak didukung. Harus file MKV.', 'error')
        return redirect(url_for('index'))

@app.route('/status/<job_id>')
def conversion_status(job_id):
    output_filename = request.args.get('output_filename', 'video.mp4')
    status = conversion_status.get(job_id, {'status': 'unknown'})
    
    return render_template('result.html', 
                         status=status, 
                         job_id=job_id,
                         output_filename=output_filename)

@app.route('/progress/<job_id>')
def get_progress(job_id):
    status = conversion_status.get(job_id, {'status': 'unknown', 'progress': 0, 'message': ''})
    return status

@app.route('/download/<filename>')
def download_file(filename):
    file_path = os.path.join(app.config['CONVERTED_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        flash('File tidak ditemukan', 'error')
        return redirect(url_for('index'))

@app.route('/cleanup')
def cleanup_files():
    """Hapus semua file yang sudah dikonversi (opsional)"""
    try:
        for folder in [UPLOAD_FOLDER, CONVERTED_FOLDER]:
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
        flash('Semua file berhasil dihapus', 'success')
    except Exception as e:
        flash(f'Error saat menghapus file: {str(e)}', 'error')
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
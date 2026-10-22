import re
import subprocess
import uuid
from pathlib import Path
from threading import Lock, Thread

import imageio_ffmpeg
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

STORAGE = Path(__file__).resolve().parent / 'storage'
STORAGE.mkdir(exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

jobs = {}
lock = Lock()


def buscar_job(job_id):
    with lock:
        job = jobs.get(job_id)
    if job is None:
        return None, (jsonify(error='Job não encontrado'), 404)
    return job, None


def obter_fps(arquivo):
    processo = subprocess.run(
        [FFMPEG, '-hide_banner', '-i', str(arquivo)],
        capture_output=True, text=True, errors='replace', timeout=30
    )
    for linha in processo.stderr.splitlines():
        if 'Video:' in linha:
            encontrado = re.search(r'(\d+(?:\.\d+)?)\s+fps\b', linha)
            if encontrado:
                fps = float(encontrado.group(1))
                if 0 < fps <= 240:
                    return fps
    raise ValueError('Não foi possível identificar o FPS do vídeo.')


def verificar_frames(pasta, quantidade_esperada=None):
    arquivos = sorted(pasta.iterdir())
    if not arquivos:
        raise ValueError('Nenhum frame recebido.')

    numeros = []
    extensoes = set()
    for arquivo in arquivos:
        if not arquivo.is_file() or not re.fullmatch(r'\d{6}\.(jpg|png)', arquivo.name):
            raise ValueError(f'Nome de frame inválido: {arquivo.name}')
        numeros.append(int(arquivo.stem))
        extensoes.add(arquivo.suffix)

    numeros.sort()
    if len(extensoes) != 1 or numeros[0] not in (0, 1):
        raise ValueError('Os frames devem iniciar em 0 ou 1 e ter a mesma extensão.')
    if numeros != list(range(numeros[0], numeros[0] + len(numeros))):
        raise ValueError('Existem frames duplicados ou faltando na sequência.')
    if quantidade_esperada is not None and len(numeros) != quantidade_esperada:
        raise ValueError(f'Esperados {quantidade_esperada} frames, recebidos {len(numeros)}.')
    return numeros[0], extensoes.pop(), len(numeros)


@app.post('/videos')
def enviar_video():
    arquivo = request.files.get('video')
    if not arquivo or not arquivo.filename:
        return jsonify(error="Envie um arquivo no campo 'video'."), 400

    extensao = Path(secure_filename(arquivo.filename)).suffix.lower()
    if extensao not in {'.mp4', '.mov', '.avi', '.mkv', '.webm'}:
        return jsonify(error='Formato de vídeo não permitido.'), 400

    job_id = uuid.uuid4().hex
    pasta = STORAGE / job_id
    (pasta / 'frames').mkdir(parents=True)
    original = pasta / f'original{extensao}'
    try:
        arquivo.save(original)
        fps = obter_fps(original)
    except (OSError, ValueError, subprocess.TimeoutExpired) as erro:
        import shutil
        shutil.rmtree(pasta, ignore_errors=True)
        return jsonify(error=str(erro)), 400

    with lock:
        jobs[job_id] = {'status': 'waiting_frames', 'original': original,
                        'fps': fps, 'result': None, 'error': None}
    return jsonify(job_id=job_id, status='waiting_frames', original_fps=fps), 201


@app.get('/videos/<job_id>/original')
def baixar_original(job_id):
    job, erro = buscar_job(job_id)
    if erro:
        return erro
    return send_file(job['original'], as_attachment=True)


@app.post('/videos/<job_id>/frames')
def enviar_frames(job_id):
    job, erro = buscar_job(job_id)
    if erro:
        return erro
    arquivos = request.files.getlist('frames')
    if not arquivos:
        return jsonify(error="Envie os arquivos no campo 'frames'."), 400

    pasta = STORAGE / job_id / 'frames'
    preparados = []
    for arquivo in arquivos:
        nome = secure_filename(arquivo.filename or '')
        match = re.fullmatch(r'(\d+)\.(jpg|jpeg|png)', nome, re.I)
        if not match:
            return jsonify(error=f'Nome inválido: {nome}'), 400
        extensao = '.jpg' if match.group(2).lower() == 'jpeg' else '.' + match.group(2).lower()
        preparados.append((arquivo, f'{int(match.group(1)):06d}{extensao}'))

    nomes = [nome for _, nome in preparados]
    if len(nomes) != len(set(nomes)):
        return jsonify(error='Frames duplicados no envio.'), 400

    with lock:
        if job['status'] != 'waiting_frames':
            return jsonify(error='Este job não aceita mais frames.'), 409
        existentes = {p.name for p in pasta.iterdir()}
        numeros_existentes = {int(p.stem) for p in pasta.iterdir() if p.is_file() and p.stem.isdigit()}
        numeros_novos = {int(Path(nome).stem) for nome in nomes}
        if existentes.intersection(nomes) or numeros_existentes.intersection(numeros_novos):
            return jsonify(error='Um ou mais números de frame já foram enviados.'), 409
        extensoes = {Path(nome).suffix for nome in nomes} | {Path(nome).suffix for nome in existentes}
        if len(extensoes) != 1:
            return jsonify(error='Todos os frames devem ter a mesma extensão.'), 400
        try:
            for arquivo, nome in preparados:
                arquivo.save(pasta / nome)
        except OSError as exc:
            return jsonify(error=f'Falha ao salvar frames: {exc}'), 500
    return jsonify(received=len(arquivos)), 200


def montar_video(job_id, fps, quantidade_esperada):
    pasta = STORAGE / job_id
    resultado = pasta / 'resultado.mp4'
    try:
        inicio, extensao, quantidade = verificar_frames(pasta / 'frames', quantidade_esperada)
        comando = [
            FFMPEG, '-y', '-framerate', str(fps), '-start_number', str(inicio),
            '-i', str(pasta / 'frames' / f'%06d{extensao}'),
            '-frames:v', str(quantidade), '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(resultado)
        ]
        subprocess.run(comando, check=True, capture_output=True, timeout=1800)
        if not resultado.is_file() or resultado.stat().st_size == 0:
            raise RuntimeError('O FFmpeg não gerou o vídeo.')
        with lock:
            jobs[job_id].update(status='done', result=resultado, error=None)
    except Exception as exc:
        app.logger.exception('Falha ao montar vídeo')
        resultado.unlink(missing_ok=True)
        mensagem = (exc.stderr or b'').decode(errors='replace')[-1000:] if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        with lock:
            jobs[job_id].update(status='error', result=None, error=mensagem)


@app.post('/videos/<job_id>/finalize')
def finalizar(job_id):
    job, erro = buscar_job(job_id)
    if erro:
        return erro
    try:
        fps = float(request.args.get('fps', job['fps']))
        if not 0 < fps <= 240:
            raise ValueError('FPS fora do intervalo permitido.')
        esperado = request.args.get('expected_frames')
        esperado = int(esperado) if esperado is not None else None
        if esperado is not None and esperado <= 0:
            raise ValueError('expected_frames deve ser positivo.')
    except (ValueError, TypeError, OverflowError):
        return jsonify(error='FPS ou expected_frames inválido.'), 400

    with lock:
        if job['status'] != 'waiting_frames':
            return jsonify(error=f"Status atual: {job['status']}"), 409
        try:
            verificar_frames(STORAGE / job_id / 'frames', esperado)
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 400
        job['status'] = 'assembling'

    try:
        Thread(target=montar_video, args=(job_id, fps, esperado), daemon=True).start()
    except Exception as exc:
        with lock:
            job.update(status='error', error=str(exc))
        return jsonify(error='Não foi possível iniciar a montagem.'), 500
    return jsonify(status='assembling', fps=fps), 202


@app.get('/videos/<job_id>/status')
def consultar_status(job_id):
    job, erro = buscar_job(job_id)
    if erro:
        return erro
    with lock:
        return jsonify(status=job['status'], error=job['error'])


@app.get('/videos/<job_id>/result')
def baixar_resultado(job_id):
    job, erro = buscar_job(job_id)
    if erro:
        return erro
    with lock:
        status, resultado = job['status'], job['result']
    if status != 'done':
        return jsonify(error='Vídeo ainda não está pronto.', status=status), 409
    if not resultado or not resultado.is_file():
        return jsonify(error='Vídeo final não encontrado.'), 404
    return send_file(resultado, mimetype='video/mp4')


@app.errorhandler(413)
def arquivo_grande(erro):
    return jsonify(error='Limite de 500 MB por requisição excedido.'), 413


if __name__ == '__main__':
    app.run(port=5000, debug=False)

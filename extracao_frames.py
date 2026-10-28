import cv2
from pathlib import Path
import datetime

BASE_DIR = Path(__file__).resolve().parent

pasta_entrada = BASE_DIR / "VIDS_ORIGINAIS"
pasta_saida = BASE_DIR / "FRAMES_SAIDA"

EXTENSOES = {
    ".mp4", ".avi", ".mov", ".mkv",
    ".wmv", ".mpeg", ".mpg", ".m4v"
}

print("Diretório atual:", Path.cwd())
print("Pasta de entrada existe:", pasta_entrada.exists())
print("Pasta de saída:", pasta_saida.resolve())

def listar_videos(pasta):
    videos_encontrados = []
    for arquivo in pasta.rglob("*"):
        if arquivo.is_file() and arquivo.suffix.lower() in EXTENSOES:
            videos_encontrados.append(arquivo)
    return sorted(videos_encontrados)

def extrair_frames(caminho_video):
    caminho_relativo = caminho_video.relative_to(pasta_entrada)
    nome_pasta = (caminho_relativo.parent / f'{caminho_video.stem}')

    pasta_frames = pasta_saida / nome_pasta
    pasta_frames.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(caminho_video))

    if not cap.isOpened():
        print(f"Não foi possível abrir o vídeo: {caminho_video}")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    altura = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    largura = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    segundos = round(frames/fps)
    duracao_video = datetime.timedelta(seconds=segundos)

    num_frame = 0
    total_salvo = 0
    proxima_coleta = 0

    while True:
        saida, frame = cap.read()

        if not saida:
            break

        if num_frame >= round(proxima_coleta * fps):
            nome = f"frame_{proxima_coleta:04d}s.jpg"
            caminho_completo = pasta_frames / nome

            if not cv2.imwrite(str(caminho_completo), frame):
                raise IOError(f"Erro ao salvar arquivo!!!")

            total_salvo += 1
            proxima_coleta += 1

        num_frame += 1

    cap.release()
    return largura, altura, duracao_video


videos = listar_videos(pasta_entrada)

print("Quantidade de vídeos encontrados:", len(videos))
    
for video in videos:
    try:
        print(video)
        extrair_frames(video)
    except Exception as erro:
        print(f"Falha ao processar {video}: {erro}")
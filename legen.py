import argparse
import os
import subprocess
import time

import torch
import whisper

import ffmpeg_utils
import file_utils
import translate_utils
import whisper_utils

version = "v0.1"

# Terminal colors
default = "\033[1;0m"
gray = "\033[1;37m"
wblue = "\033[1;36m"
blue = "\033[1;34m"
yellow = "\033[1;33m"
green = "\033[1;32m"
red = "\033[1;31m"

print(f"""
{blue}888              {gray} .d8888b.                   
{blue}888              {gray}d88P  Y88b                  
{blue}888              {gray}888    888                  
{blue}888      .d88b.  {gray}888         .d88b.  88888b. 
{blue}888     d8P  Y8b {gray}888  88888 d8P  Y8b 888 "88b
{blue}888     88888888 {gray}888    888 88888888 888  888
{blue}888     Y8b.     {gray}Y88b  d88P Y8b.     888  888
{blue}88888888 "Y8888  {gray} "Y8888P88  "Y8888  888  888

{version} - github.com/matheusbach/legen{default}
""")
time.sleep(1.5)

# define parâmetros e configuraçṍes
parser = argparse.ArgumentParser(prog="LeGen", description="Normaliza arquivos de vídeo, transcreve legendas a partir do áudio, traduz as legendas geradas, salva as legendas em arquivos .srt, insere no container mp4 e queima diretamente no vídeo",
                                 argument_default=True, allow_abbrev=True, add_help=True)
parser.add_argument("-i", "--input_dir", type=str,
                    help="caminho da pasta onde os vídeos originais estão localizados.", required=True)
parser.add_argument("--model", type=str, default="base",
                    help="caminho ou nome do modelo de transcrição Whisper. (default: base)")
parser.add_argument("--dev", type=str, default="auto",
                    help="dispositivo para rodar a transcrição pelo Whisper. [cpu, cuda, auto]. (default: auto)")
parser.add_argument("--lang", type=str, default="pt",
                    help="idioma para o qual as legendas devem ser traduzidas. (default: pt)")
parser.add_argument("--crf", type=int, default=20,
                    help="valor CRF a ser usado no vídeo. (default: 20)")
parser.add_argument("--maxrate", type=str, default="2M",
                    help="maxrate a ser usado no vídeo. (default: 2M)")
parser.add_argument("--srt_out_dir", type=str, default=None,
                    help="caminho da pasta de saída para os arquivos de vídeo com legenda embutida no container mp4 e arquivos SRT. (default: legen_srt_$input_dir)")
parser.add_argument("--burned_out_dir", type=str, default=None,
                    help="caminho da pasta de saída para os arquivos de vídeo com legendas queimadas no vídeo e embutidas no container mp4. (default: legen_burned_$lang_$input_dir)")
parser.add_argument("--overwrite", default=False, action="store_true",
                    help="overwrite existing files in output dirs")
parser.add_argument("--disable_srt", default=False, action="store_true",
                    help="disable .srt file generation and don't insert subtitles in mp4 container of $srt_out_dir")
parser.add_argument("--disable_burn", default=False, action="store_true",
                    help="disable subtitle burn in $burned_out_dir")
parser.add_argument("--only_video", default=False, action="store_true",
                    help="don't copy other files present in input dir to output dirs")
args = parser.parse_args()

input_dir = args.input_dir
if args.srt_out_dir is None:
    args.srt_out_dir = os.path.join(
        *(os.path.split(input_dir)[:-1] + (f'legen_srt_{os.path.split(input_dir)[-1]}',)))
srt_out_dir = args.srt_out_dir
if args.burned_out_dir is None:
    args.burned_out_dir = os.path.join(
        *(os.path.split(input_dir)[:-1] + (f'legen_burned_{args.lang}_{os.path.split(input_dir)[-1]}',)))
burned_out_dir = args.burned_out_dir
torch_device = ("cuda" if torch.cuda.is_available()
                else "cpu") if args.dev == "auto" else args.dev
disable_fp16 = False if args.dev == "cpu" else True

# ----------------------------------------------------------------------------

# normalize video using vidqa
print(f"Running {wblue}vidqa{default} in {gray}{input_dir}{default}")
subprocess.run(["vidqa", "-i", input_dir, "-m", "unique", "-fd",
               os.path.join(os.path.realpath(os.path.dirname(__file__)), "vidqa_data")])

# load whisper model
print(f"\nLoading Whisper model: {wblue}{args.model}{default} on {wblue}{torch_device}{default}")
whisper_model = whisper.load_model(args.model, device=torch_device)

for dirpath, dirnames, filenames in os.walk(input_dir):
    for filename in sorted(filenames):
        rel_path = os.path.relpath(dirpath, input_dir)
        print(
            f"\nProcessing {yellow}{os.path.join(rel_path, filename)}{default}")
        # only mp4 because vidqa has converted all to mp4
        if filename.endswith((".mp4")):
            # define paths
            origin_video_path = os.path.join(input_dir, rel_path, filename)
            srt_video_dir = os.path.join(srt_out_dir, rel_path)
            burned_video_dir = os.path.join(burned_out_dir, rel_path)
            srt_video_path = os.path.join(srt_video_dir, filename)
            burned_video_path = os.path.join(burned_video_dir, filename)
            subtitle_translated_path = os.path.join(
                srt_video_dir, f"{os.path.splitext(filename)[0]}_{args.lang}.srt")

            # transcribe video audio and save original subtitle
            print(f"{wblue}Transcribing{default} with {gray}Whisper{default}")

            # extract audio
            audio_extracted = file_utils.TempFile(None, file_ext=".mp3")
            ffmpeg_utils.extract_audio_mp3(
                origin_video_path, audio_extracted.getname())

            # detect language
            print("Detecting audio language", end='')
            audio_language = whisper_utils.detect_language(
                whisper_model, audio_extracted.getname())
            
            # set path after get transcribed language
            subtitle_transcribed_path = os.path.join(
                srt_video_dir, f"{os.path.splitext(filename)[0]}_{audio_language}.srt")
            print(f": {gray}{audio_language}{default}")
            # create temp file for .srt
            transcribed_srt_temp = file_utils.TempFile(
                subtitle_transcribed_path, file_ext=".srt")

            # skip transcription if transcribed srt for this language is existing and overwrite is disabled
            if file_utils.file_is_valid(os.path.join(
                    srt_video_dir, f"{os.path.splitext(filename)[0]}_{audio_language}.srt")) and not args.overwrite:
                print(
                    f"Existing .srt file for language {gray}{audio_language}{default}. Skipping transcription")
            else:
                # transcribe saving subtitles to temp .srt file
                print(f"Running Whisper transcription for speech reconition")
                transcribe = whisper_utils.transcribe_audio(
                    whisper_model, audio_extracted.getname(), transcribed_srt_temp.getname(), audio_language, disable_fp16)
                
                # if save .srt is enabled, save it to destination dir, also update path with language code
                if not args.disable_srt:
                    transcribed_srt_temp.save()
            audio_extracted.destroy()

            # translate transcribed subtitle using Google Translate if transcribed language is not equals to target
            if args.lang == audio_language:
                print("Transcribed language {gray}{audio_language}{default} is the same as target language {gray}{args.lang}{default}. Skipping translation.")
            else:
                # create the temp .srt translated file
                translated_srt_temp = file_utils.TempFile(
                    subtitle_translated_path, file_ext=".srt")
                
                # skip transcription if transcribed srt for this language is existing and overwrite is disabled
                if file_utils.file_is_valid(translated_srt_temp.final_path) and not args.overwrite:
                    print(f"Existing .srt file for language {gray}{args.lang}{default}. Skipping translation")
                else:
                    # translating with google translate public API
                    print(f"{wblue}Translating{default} with {gray}Google Translate{default}")
                    subs = translate_utils.translate_srt_file(
                        transcribed_srt_temp.getvalidname(), translated_srt_temp.getname(), args.lang)
                    if not args.disable_srt:
                        translated_srt_temp.save()

            if not args.disable_srt:
                if file_utils.file_is_valid(srt_video_path) and not args.overwrite:
                    print(f"Existing video file {gray}{srt_video_path}{default}. Skipping subtitle insert")
                else:
                    # create the temp .mp4 with srt in video container
                    video_srt_temp = file_utils.TempFile(
                        srt_video_path, file_ext=".mp4")
                    
                    # insert subtitle into container using ffmpeg
                    print(f"{wblue}Inserting subtitle{default} in mp4 container using {gray}FFmpeg{default}")
                    ffmpeg_utils.insert_subtitle(origin_video_path, [translated_srt_temp.getvalidname(), transcribed_srt_temp.getvalidname()],
                                                False, video_srt_temp.getname(), args.crf, args.maxrate)
                    video_srt_temp.save()

            if not args.disable_burn:
                if file_utils.file_is_valid(burned_video_path) and not args.overwrite:
                    print(
                        f"Existing video file {gray}{burned_video_path}{default}. Skipping subtitle burn")
                else:
                    # create the temp .mp4 with srt in video container
                    video_burned_temp = file_utils.TempFile(
                        burned_video_path, file_ext=".mp4")
                    # insert subtitle into container and burn using ffmpeg
                    print(f"{wblue}Inserting subtitle{default} in mp4 container and {wblue}burning{default} using {gray}FFmpeg{default}")
                    ffmpeg_utils.insert_subtitle(origin_video_path, [translated_srt_temp.getvalidname(), transcribed_srt_temp.getvalidname()],
                                                True, video_burned_temp.getname(), args.crf, args.maxrate)
                    video_burned_temp.save()
        else:
            print("not a video file")
            if not args.only_video:
                if not args.disable_srt:
                    # copia o arquivo extra para pasta que contém também os arquivos srt
                    file_utils.copy_file_if_different(os.path.join(input_dir, rel_path, filename), os.path.join(
                        srt_out_dir, rel_path, filename))
                if not args.disable_burn:
                    # copia o arquivo extra para pasta que contém os videos queimados
                    file_utils.copy_file_if_different(os.path.join(input_dir, rel_path, filename), os.path.join(
                        burned_out_dir, rel_path, filename))

print("Deleting temp folder")
file_utils.delete_folder(os.path.join(
    os.path.realpath(os.path.dirname(__file__)), "temp"))

print(f"{green}Processamento concluído!{default}")
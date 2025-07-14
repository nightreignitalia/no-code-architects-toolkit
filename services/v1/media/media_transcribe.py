# Copyright (c) 2025 Stephen G. Pope
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
"""services.v1.media.media_transcribe
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Estende la funzione originale per accettare un *initial_prompt* opzionale.
Se presente, il prompt viene passato a Whisper per ancorare la decodifica
(evitando errori su inglesismi, brand, ecc.).  In assenza del parametro il
comportamento rimane identico alla versione originaria.
"""

import os
import whisper
import srt
from datetime import timedelta
from whisper.utils import WriteSRT, WriteVTT
from services.file_management import download_file
import logging
from config import LOCAL_STORAGE_PATH

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def process_transcribe_media(
    media_url: str,
    task: str,
    include_text: bool,
    include_srt: bool,
    include_segments: bool,
    word_timestamps: bool,
    response_type: str,
    language: str | None,
    initial_prompt: str | None,   # <<< nuovo parametro
    job_id: str,
    words_per_line: int | None = None,
):
    """Transcribe or translate *media_url* using Whisper.

    Args:
        media_url: URL HTTP/HTTPS del file su cui lavorare.
        task: "transcribe" | "translate".
        include_text, include_srt, include_segments: flag di output.
        word_timestamps: se True chiede timestamp per parola.
        response_type: "direct" (restituisce i file) oppure
                        "cloud" (salva in LOCAL_STORAGE_PATH).
        language: codice ISO-639-1 ("it", "en" …) oppure None per autodetect.
        initial_prompt: testo di contesto da dare al decoder Whisper.
        job_id: ID univoco per i file temporanei.
        words_per_line: wrap automatico per SRT.
    """

    logger.info("Starting %s for media URL: %s", task, media_url)
    input_filename = download_file(
        media_url, os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_input")
    )
    logger.info("Downloaded media to local file: %s", input_filename)

    try:
        # Carica un modello base (puoi passare a "large" se servono traduzioni)
        model_size = "base"
        model = whisper.load_model(model_size)
        logger.info("Loaded Whisper %s model", model_size)

        # Opzioni di transcodifica
        options: dict = {
            "task": task,
            "word_timestamps": word_timestamps,
            "verbose": False,
        }
        if language:
            options["language"] = language
        if initial_prompt:
            # Il parametro viene troncato da Whisper a ≈224 token se più lungo
            options["initial_prompt"] = initial_prompt
            logger.info("Using initial_prompt (%.0f chars)", len(initial_prompt))

        # --- TRASCRIZIONE ---------------------------------------------------
        result = model.transcribe(input_filename, **options)

        text = srt_text = segments_json = None

        if include_text:
            text = result["text"]

        if include_srt:
            srt_subtitles = []
            subtitle_index = 1
            if words_per_line and words_per_line > 0:
                # Suddividi in blocchi fissi di parole
                all_words: list[str] = []
                word_timings: list[tuple[float, float]] = []
                for segment in result["segments"]:
                    words = segment["text"].strip().split()
                    start, end = segment["start"], segment["end"]
                    if not words:
                        continue
                    dur = (end - start) / len(words)
                    for i, w in enumerate(words):
                        w_start = start + i * dur
                        word_timings.append((w_start, w_start + dur))
                        all_words.append(w)
                current = 0
                while current < len(all_words):
                    chunk = all_words[current : current + words_per_line]
                    c_start = word_timings[current][0]
                    c_end = word_timings[min(current + len(chunk) - 1, len(word_timings) - 1)][1]
                    srt_subtitles.append(
                        srt.Subtitle(
                            subtitle_index,
                            timedelta(seconds=c_start),
                            timedelta(seconds=c_end),
                            " ".join(chunk),
                        )
                    )
                    subtitle_index += 1
                    current += words_per_line
            else:
                # Un sottotitolo per segmento
                for segment in result["segments"]:
                    srt_subtitles.append(
                        srt.Subtitle(
                            subtitle_index,
                            timedelta(seconds=segment["start"]),
                            timedelta(seconds=segment["end"]),
                            segment["text"].strip(),
                        )
                    )
                    subtitle_index += 1
            srt_text = srt.compose(srt_subtitles)

        if include_segments:
            segments_json = result["segments"]

        # --------------------------------------------------------------------
        os.remove(input_filename)
        logger.info("Removed local file: %s", input_filename)

        if response_type == "direct":
            return text, srt_text, segments_json

        # Salva nel filesystem locale per poi caricarlo su GCS
        text_filename = srt_filename = segments_filename = None
        if include_text:
            text_filename = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}.txt")
            with open(text_filename, "w") as f:
                f.write(text)
        if include_srt:
            srt_filename = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}.srt")
            with open(srt_filename, "w") as f:
                f.write(srt_text)
        if include_segments:
            segments_filename = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}.json")
            with open(segments_filename, "w") as f:
                f.write(str(segments_json))

        return text_filename, srt_filename, segments_filename

    except Exception as e:
        logger.exception("%s failed: %s", task.capitalize(), e)
        raise

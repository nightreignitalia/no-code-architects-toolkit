FROM stephengpope/no-code-architects-toolkit:latest

COPY requirements.txt /tmp/requirements.txt
RUN  pip install --no-cache-dir -r /tmp/requirements.txt

COPY routes/v1/video/merge_audio.py /app/routes/v1/video/merge_audio.py

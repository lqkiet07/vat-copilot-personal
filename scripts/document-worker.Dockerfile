FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        tesseract-ocr \
        tesseract-ocr-vie \
        tesseract-ocr-eng \
        poppler-utils \
        libarchive-tools \
        python3 \
        fonts-dejavu-core \
    && apt-get clean \
    && useradd --uid 10001 --create-home worker

ENV HOME=/tmp/home
ENV TMPDIR=/tmp
USER 10001:10001
WORKDIR /tmp
CMD ["python3", "--version"]

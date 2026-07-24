FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV QT_QPA_PLATFORM=offscreen

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        calibre \
        fonts-dejavu-core \
        fonts-noto-cjk \
        fonts-noto-core \
        lmodern \
        make \
        pandoc \
        poppler-utils \
        python3 \
        python3-pil \
        texlive-fonts-recommended \
        texlive-lang-chinese \
        texlive-latex-extra \
        texlive-xetex \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /book

CMD ["python3", "scripts/build_book.py", "build", "--engine", "local"]

# Build environment for the aarch64 Linux binary of kioskium.
#
# Ubuntu 20.04 (glibc 2.31) so the result runs on Yocto hardknott images (glibc 2.33) and newer.
# PySide6 6.8.0.2 is the last release with manylinux_2_31 aarch64 wheels; it needs Python 3.9.
# The resulting one-folder bundle carries its own Python, Qt 6.8 and Chromium 122 and only needs
# the device's EGL/GLES, Wayland, X11-client, fontconfig/freetype and glibc.
FROM arm64v8/ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.9 libpython3.9 python3.9-venv python3.9-distutils python3-pip ca-certificates binutils git \
        # libraries Qt WebEngine links; PyInstaller bundles the ones it does not treat as system libs
        libxkbcommon0 libxkbcommon-x11-0 libnss3 libnspr4 libasound2 libpulse0 libdbus-1-3 \
        libfontconfig1 libfreetype6 libglib2.0-0 libexpat1 libbrotli1 libminizip1 libwebp6 libwebpdemux2 \
        libwebpmux3 libxslt1.1 libxml2 libicu66 libopus0 libevent-2.1-7 libcups2 libtiff5 libjpeg8 libpng16-16 \
        libwayland-client0 libwayland-egl1 libwayland-cursor0 libdrm2 libgbm1 libudev1 libzstd1 \
        libx11-6 libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-glx0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 \
        libxcb-xinerama0 libxcb-xkb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxi6 libxkbfile1 \
        libxrandr2 libxrender1 libxshmfence1 libxtst6 libgl1 libegl1 libglx0 libglvnd0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python3.9 -m pip install --no-cache-dir --upgrade pip \
    && python3.9 -m pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /src
# libglvnd's libGL is copied into the bundle so it shadows the Vivante desktop-GL library on i.MX
# (Qt's Wayland EGL swapBuffers calls glFlush through libGL; see docs/EMBEDDED.md).
ENV KIOSKIUM_EXTRA_LIBS=/usr/lib/aarch64-linux-gnu/libGL.so.1:/usr/lib/aarch64-linux-gnu/libGLX.so.0:/usr/lib/aarch64-linux-gnu/libGLdispatch.so.0
# The version file comes from the git tags (setuptools-scm); the mounted checkout belongs to another user.
CMD ["sh", "-c", "git config --global --add safe.directory /src && python3.9 -m setuptools_scm --force-write-version-files && python3.9 -m PyInstaller packaging/pyinstaller/kioskium.spec --noconfirm --clean --distpath dist/linux-arm64 --workpath /tmp/pyi-build && cd dist/linux-arm64 && tar -czf kiosk-linux-arm64.tar.gz kiosk && ls -la"]

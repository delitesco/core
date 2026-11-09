FROM --platform=linux/amd64 ubuntu:22.04

ARG UV_VERSION="0.7.13"
ARG UV_HASH="909278eb197c5ed0e9b5f16317d1255270d1f9ea4196e7179ce934d48c4c2545"
# the production image leaves out the development tools; the test image passes ""
ARG UV_SYNC_ARGS="--no-dev"

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1
# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1
# outside /root, so that the unprivileged user can run the interpreter
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python


WORKDIR /code

# Copy dependency files
COPY pyproject.toml uv.lock .python-version ./

# Install deps
RUN apt-get update \
    && apt-get install -y curl netcat-traditional gcc python3-dev gnupg git libre2-dev build-essential pkg-config cmake ninja-build bash clang \
    && curl -sSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" > uv.tar.gz \
    && echo "${UV_HASH}  uv.tar.gz" | sha256sum -c - \
    && tar xf uv.tar.gz -C /tmp/ \
    && mv /tmp/uv-x86_64-unknown-linux-gnu/uv /usr/bin/uv \
    && mv /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/bin/uvx \
    && rm -rf /tmp/uv* \
    && rm -f uv.tar.gz \
    && uv python install `cat .python-version` \
    && export CMAKE_POLICY_VERSION_MINIMUM=3.5 \
    && uv sync --locked ${UV_SYNC_ARGS} \
    && apt-get autoremove -y \
    && apt-get purge -y curl netcat-traditional build-essential pkg-config cmake ninja-build python3-dev clang\
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy code
COPY . .

ENV PATH="/code/.venv/bin:$PATH"

# run as an unprivileged user; the data volume (/sl) is handed over by deploy/init.sh
RUN groupadd --system --gid 10001 delitesco \
    && useradd --system --uid 10001 --gid 10001 --home-dir /code --no-create-home delitesco \
    && mkdir -p /sl \
    && chown delitesco:delitesco /sl
USER delitesco

EXPOSE 7777

#gunicorn wsgi:app -b 0.0.0.0:7777 -w 2 --timeout 15 --log-level DEBUG
CMD ["gunicorn","wsgi:app","-b","0.0.0.0:7777","-w","2","--timeout","15"]

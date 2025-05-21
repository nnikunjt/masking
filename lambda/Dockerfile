# Use AWS Lambda base image for Python 3.11
FROM public.ecr.aws/lambda/python:3.11

# Define build arguments for non-sensitive data
ARG AWS_DEFAULT_REGION=us-east-1
ARG AWS_S3_BUCKET=masked-certificates-bucket
ARG AWS_S3_REGION=us-east-1

# Install basic tools and dependencies in a single layer to reduce image size
RUN yum update -y && \
    yum install -y \
    curl tar gzip which \
    gcc gcc-c++ make \
    glibc glibc-devel \
    libffi-devel zlib-devel \
    autoconf automake libtool pkg-config \
    libjpeg-devel libpng-devel libtiff-devel \
    libwebp-devel giflib-devel \
    cairo-devel pango-devel \
    gettext gettext-devel ImageMagick-devel \
    glibc-common \
    ghostscript   gtk2-devel wget  openssl openssl-devel && \
    yum clean all && \
    rm -rf /var/cache/yum

# Create AWS config directory with region only
RUN mkdir -p /root/.aws && \
    echo "[default]\nregion = ${AWS_DEFAULT_REGION}\noutput = json" > /root/.aws/config

# Set non-sensitive environment variables
ENV AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION}
ENV AWS_S3_BUCKET=${AWS_S3_BUCKET}
ENV AWS_S3_REGION=${AWS_S3_REGION}

# Download and build zbar from source
RUN wget https://github.com/mchehab/zbar/archive/refs/tags/0.23.1.tar.gz -O zbar.tar.gz && \
    tar -xzf zbar.tar.gz && \
    cd zbar-0.23.1 && \
    autoreconf -fi && \
    ./configure --without-gtk --without-qt --without-imagemagick --with-python=yes --disable-video && \
    make && \
    make install && \
    cd .. && rm -rf zbar.tar.gz zbar-0.23.1


RUN cp /usr/local/lib/libzbar.so /usr/lib64/

# Setup environment variables early so they're available throughout the build
ENV TESSDATA_PREFIX=/usr/local/share/tessdata
ENV PATH="/usr/local/bin:${PATH}"
ENV LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH}"
ENV PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH}"

# Create directories needed for installation
RUN mkdir -p /usr/local/share/tessdata

# Install Leptonica from source
RUN curl -L https://github.com/DanBloomberg/leptonica/releases/download/1.82.0/leptonica-1.82.0.tar.gz -o leptonica-1.82.0.tar.gz && \
    tar -xzf leptonica-1.82.0.tar.gz && \
    cd leptonica-1.82.0 && \
    ./configure --prefix=/usr/local && \
    make -j$(nproc) && \
    make install && \
    cd .. && \
    rm -rf leptonica-1.82.0 leptonica-1.82.0.tar.gz

# Install Tesseract from source
RUN curl -L https://github.com/tesseract-ocr/tesseract/archive/refs/tags/5.3.0.tar.gz -o 5.3.0.tar.gz && \
    tar -xzf 5.3.0.tar.gz && \
    cd tesseract-5.3.0 && \
    ./autogen.sh && \
    ./configure --prefix=/usr/local --disable-openmp && \
    make -j$(nproc) && \
    make install && \
    cd .. && \
    rm -rf tesseract-5.3.0 5.3.0.tar.gz

# Update the dynamic linker cache
RUN echo "/usr/local/lib" > /etc/ld.so.conf.d/local.conf && \
    ldconfig || echo "ldconfig not available, skipping"

# Download and install Tesseract trained data
RUN curl -L https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata -o ${TESSDATA_PREFIX}/eng.traineddata

# Set working directory
# WORKDIR ${LAMBDA_TASK_ROOT}
WORKDIR /var/task

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the function code (reduces rebuild time when only code changes)
COPY lambda_function.py .

# Set the CMD for AWS Lambda
CMD ["lambda_function.lambda_handler"]
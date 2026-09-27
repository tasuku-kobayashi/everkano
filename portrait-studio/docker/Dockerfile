# syntax=docker/dockerfile:1
# =============================================================================
# ComfyUI for portrait-studio — RTX 5070 (Blackwell, sm_120) needs a CUDA 12.8 build of PyTorch >= 2.7.
#
# Base image: the official PyTorch image with the cuda12.8 tag. Override when the tag moves:
#   docker compose build --build-arg PYTORCH_IMAGE=pytorch/pytorch:<newer>-cuda12.8-cudnn9-devel
# Custom nodes (pinned by commit in docker/custom_nodes.lock):
#   ComfyUI-Manager is NOT installed on purpose (no unattended downloads); models come from scripts/install_models.sh.
#   - cubiq/PuLID_ComfyUI            ApplyPulid, PulidModelLoader, PulidInsightFaceLoader, PulidEvaClipLoader
#   - cubiq/ComfyUI_IPAdapter_plus   IPAdapterUnifiedLoaderFaceID, IPAdapterFaceID
#   - cubiq/ComfyUI_InstantID        ApplyInstantID, InstantIDModelLoader, InstantIDFaceAnalysis
#   - ltdrdata/ComfyUI-Impact-Pack   FaceDetailer
#   - ltdrdata/ComfyUI-Impact-Subpack UltralyticsDetectorProvider (bbox/face_yolov8m.pt)
# insightface inside ComfyUI uses onnxruntime (CPU build) — the GPU build is never installed (sm_120 kernels missing
# in some builds; the face models are light enough on CPU).
# Verify after build (G0/G1):
#   docker compose run --rm comfyui python -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))"
# =============================================================================
ARG PYTORCH_IMAGE=pytorch/pytorch:2.11.0-cuda12.8-cudnn9-devel
FROM ${PYTORCH_IMAGE}

ARG COMFYUI_REF=master
ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    COMFYUI_DIR=/opt/ComfyUI

# git for cloning, build-essential for insightface (built from source on some Python versions), libgl for opencv
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential cmake libgl1 libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone https://github.com/comfyanonymous/ComfyUI.git ${COMFYUI_DIR} \
    && cd ${COMFYUI_DIR} && git checkout ${COMFYUI_REF}

# ComfyUI requirements without letting pip replace the CUDA 12.8 torch from the base image
WORKDIR ${COMFYUI_DIR}
RUN grep -v -E '^(torch|torchvision|torchaudio|torchsde)' requirements.txt > /tmp/req.txt \
    && pip install -r /tmp/req.txt torchsde

# Custom nodes, pinned by commit (see docker/custom_nodes.lock: "<repo_url> <commit>")
COPY docker/custom_nodes.lock /tmp/custom_nodes.lock
COPY docker/install_custom_nodes.sh /tmp/install_custom_nodes.sh
RUN bash /tmp/install_custom_nodes.sh ${COMFYUI_DIR}/custom_nodes /tmp/custom_nodes.lock

# onnxruntime: CPU build only (never onnxruntime-gpu). Installed last so no custom node's requirements can swap it.
RUN pip uninstall -y onnxruntime-gpu onnxruntime || true && pip install "onnxruntime>=1.18" && pip check || true

# Run as an unprivileged user (ComfyUI's /prompt executes arbitrary workflows; a node bug must not own the container).
# The uid/gid default to 1000 = the first WSL2 user, so the bind-mounted models / input / output stay writable.
ARG COMFY_UID=1000
ARG COMFY_GID=1000
RUN groupadd --gid ${COMFY_GID} comfy \
    && useradd --uid ${COMFY_UID} --gid ${COMFY_GID} --home-dir ${COMFYUI_DIR} --no-create-home --shell /usr/sbin/nologin comfy \
    && mkdir -p ${COMFYUI_DIR}/models ${COMFYUI_DIR}/input ${COMFYUI_DIR}/output ${COMFYUI_DIR}/temp ${COMFYUI_DIR}/user \
    && chown -R comfy:comfy ${COMFYUI_DIR}
USER comfy
ENV HOME=${COMFYUI_DIR}

# Models and inputs/outputs live on the host (docker-compose volumes). ComfyUI listens on the container's
# 0.0.0.0 but compose publishes it on 127.0.0.1 only.
EXPOSE 8188
VOLUME ["${COMFYUI_DIR}/models", "${COMFYUI_DIR}/input", "${COMFYUI_DIR}/output"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8188/system_stats >/dev/null || exit 1
ENTRYPOINT ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
CMD ["--preview-method", "none"]

#!/bin/sh
# Optional: install VoiceStudio's OmniVoice TTS (CPU) for `--tts omnivoice`.
# Installs to local disk ($OMNI_HOME, default /tmp/vs): torch CPU + 3.1 GB model.
# Tested 2026-11-07: works on 2 vCPU, ~18-20x slower than real time (4.5 s line = ~85 s).
set -e
OMNI_HOME=${OMNI_HOME:-/tmp/vs}
mkdir -p "$OMNI_HOME" && cd "$OMNI_HOME"
[ -d VoiceStudio ] || git clone --depth 1 https://github.com/debpalash/VoiceStudio
python3 -m venv venv
venv/bin/pip install -q torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install -q 'transformers>=5.10.0' accelerate pydub numpy soundfile huggingface_hub safetensors
venv/bin/pip install -q --no-deps -e VoiceStudio
pip install -q huggingface_hub hf_transfer
HF_HUB_ENABLE_HF_TRANSFER=1 python3 -c "from huggingface_hub import snapshot_download as s; s('k2-fsa/OmniVoice', local_dir='$OMNI_HOME/model')"
echo "export OMNIVOICE_PY=$OMNI_HOME/venv/bin/python OMNIVOICE_MODEL=$OMNI_HOME/model"

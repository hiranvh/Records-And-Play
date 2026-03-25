import os
import urllib.request
from tkinter import messagebox
import threading

MODEL_URL = "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf"
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_FILENAME = "Phi-3.5-mini-instruct-Q4_K_M.gguf"
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)
MIN_MODEL_SIZE_BYTES = 2_000_000_000


def _resolve_existing_model_path():
    if not os.path.exists(MODEL_DIR):
        return MODEL_PATH

    for entry in os.listdir(MODEL_DIR):
        if entry.lower() == MODEL_FILENAME.lower():
            return os.path.join(MODEL_DIR, entry)

    return MODEL_PATH


def _is_valid_model_file(path):
    return os.path.exists(path) and os.path.getsize(path) >= MIN_MODEL_SIZE_BYTES

def ensure_model_exists(progress_callback=None):
    """
    Checks if the GGUF model exists. If not, downloads it.
    progress_callback receives (current_bytes, total_bytes).
    """
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)

    resolved_model_path = _resolve_existing_model_path()

    if _is_valid_model_file(resolved_model_path):
        return True

    if os.path.exists(resolved_model_path):
        os.remove(resolved_model_path)

    temp_model_path = f"{MODEL_PATH}.part"
    if os.path.exists(temp_model_path):
        os.remove(temp_model_path)

    def reporthook(blocknum, blocksize, totalsize):
        if progress_callback:
            readsofar = blocknum * blocksize
            if totalsize > 0:
                progress_callback(readsofar, totalsize)

    try:
        urllib.request.urlretrieve(MODEL_URL, temp_model_path, reporthook)
        if not _is_valid_model_file(temp_model_path):
            raise Exception("Downloaded model file is incomplete or corrupted")
        os.replace(temp_model_path, MODEL_PATH)
        return True
    except Exception as e:
        if os.path.exists(temp_model_path):
            os.remove(temp_model_path)
        if os.path.exists(MODEL_PATH) and not _is_valid_model_file(MODEL_PATH):
            os.remove(MODEL_PATH)
        raise Exception(f"Failed to download model: {e}")

def get_model_path():
    return _resolve_existing_model_path()

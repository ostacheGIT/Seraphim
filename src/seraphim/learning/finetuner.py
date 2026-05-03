"""Local LoRA fine-tuning from exported SFT pairs."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REQUIRED_DEPS = ["torch", "transformers", "peft", "trl", "datasets"]


@dataclass
class FineTuneConfig:
    base_model: str = "Qwen/Qwen2.5-3B-Instruct"
    output_dir: str = "~/.seraphim/lora_adapter"
    sft_path: str = "~/.seraphim/sft_pairs.jsonl"
    epochs: int = 3
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    learning_rate: float = 2e-4
    batch_size: int = 2
    max_seq_length: int = 512
    use_unsloth: bool = False
    merge_adapter: bool = True
    push_to_ollama: bool = True
    ollama_model_name: str = "seraphim-tuned"


@dataclass
class FineTuneResult:
    success: bool
    output_dir: str
    merged_dir: Optional[str]
    ollama_model: Optional[str]
    train_loss: Optional[float]
    message: str


def check_deps() -> list[str]:
    missing = []
    for pkg in _REQUIRED_DEPS:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def _load_dataset(sft_path: str):
    from datasets import Dataset

    path = Path(sft_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"SFT pairs not found: {path}. Run 'seraphim learn export' first."
        )
    pairs = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            pairs.append({
                "text": (
                    f"### Instruction:\n{obj['instruction']}\n\n"
                    f"### Response:\n{obj['response']}"
                )
            })
    if not pairs:
        raise ValueError(
            "No SFT pairs found. Run 'seraphim learn mine' + 'seraphim learn export' first."
        )
    logger.info("Loaded %d training examples", len(pairs))
    return Dataset.from_list(pairs)


def _run_training_sync(config: FineTuneConfig) -> FineTuneResult:
    missing = check_deps()
    if missing:
        install_cmd = f"pip install {' '.join(missing)}"
        return FineTuneResult(
            success=False,
            output_dir="",
            merged_dir=None,
            ollama_model=None,
            train_loss=None,
            message=f"Missing deps: {', '.join(missing)}. Install: {install_cmd}",
        )

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import LoraConfig, get_peft_model, PeftModel, TaskType
        from trl import SFTTrainer, SFTConfig

        has_cuda = torch.cuda.is_available()
        output_dir = Path(config.output_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Loading base model: %s (cuda=%s)", config.base_model, has_cuda)
        tokenizer = AutoTokenizer.from_pretrained(
            config.base_model, trust_remote_code=True
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.model_max_length = config.max_seq_length

        use_unsloth = config.use_unsloth
        if use_unsloth:
            try:
                from unsloth import FastLanguageModel
                model, tokenizer = FastLanguageModel.from_pretrained(
                    model_name=config.base_model,
                    max_seq_length=config.max_seq_length,
                    load_in_4bit=True,
                )
                model = FastLanguageModel.get_peft_model(
                    model,
                    r=config.lora_r,
                    lora_alpha=config.lora_alpha,
                    lora_dropout=config.lora_dropout,
                    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                )
            except ImportError:
                logger.warning("unsloth not available, falling back to transformers+peft")
                use_unsloth = False

        if not use_unsloth:
            dtype = torch.float16 if has_cuda else torch.float32
            model = AutoModelForCausalLM.from_pretrained(
                config.base_model,
                torch_dtype=dtype,
                device_map="auto" if has_cuda else None,
                trust_remote_code=True,
            )
            lora_cfg = LoraConfig(
                r=config.lora_r,
                lora_alpha=config.lora_alpha,
                lora_dropout=config.lora_dropout,
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                task_type=TaskType.CAUSAL_LM,
                bias="none",
            )
            model = get_peft_model(model, lora_cfg)
            model.print_trainable_parameters()

        dataset = _load_dataset(config.sft_path)

        training_args = SFTConfig(
            output_dir=str(output_dir),
            num_train_epochs=config.epochs,
            per_device_train_batch_size=config.batch_size,
            learning_rate=config.learning_rate,
            logging_steps=10,
            save_strategy="epoch",
            fp16=has_cuda,
            bf16=False,
            use_cpu=not has_cuda,
            report_to="none",
            dataset_text_field="text",
            max_length=config.max_seq_length,
        )

        trainer = SFTTrainer(
            model=model,
            train_dataset=dataset,
            args=training_args,
            processing_class=tokenizer,
        )

        logger.info("Starting LoRA training...")
        train_result = trainer.train()
        trainer.save_model(str(output_dir))
        tokenizer.save_pretrained(str(output_dir))
        train_loss = train_result.training_loss
        logger.info("Training done. Loss: %.4f", train_loss)

        merged_dir: Optional[str] = None
        merge_warning: Optional[str] = None
        if config.merge_adapter:
            merged_path = output_dir.parent / "lora_merged"
            logger.info("Merging adapter into base model...")
            try:
                base = AutoModelForCausalLM.from_pretrained(
                    config.base_model,
                    torch_dtype=torch.float16,
                    device_map="cpu",
                    trust_remote_code=True,
                )
                merged = PeftModel.from_pretrained(base, str(output_dir))
                merged = merged.merge_and_unload()
                merged.save_pretrained(str(merged_path))
                tokenizer.save_pretrained(str(merged_path))
                merged_dir = str(merged_path)
                logger.info("Merged model saved to %s", merged_dir)
            except MemoryError:
                merge_warning = (
                    "Merge skipped: not enough RAM to load full model. "
                    f"Adapter saved at {output_dir}. "
                    "Merge on a machine with ≥16GB RAM using: "
                    "python -c \"from peft import PeftModel; from transformers import AutoModelForCausalLM; "
                    f"m=AutoModelForCausalLM.from_pretrained('{config.base_model}', torch_dtype='float16'); "
                    f"m=PeftModel.from_pretrained(m, '{output_dir}').merge_and_unload(); "
                    f"m.save_pretrained('{merged_path}')\""
                )
                logger.warning(merge_warning)

        ollama_model: Optional[str] = None
        if config.push_to_ollama and merged_dir:
            ollama_model = _create_ollama_model(config.ollama_model_name, merged_dir)

        message = "Fine-tuning complete."
        if merge_warning:
            message += f" WARNING: {merge_warning}"

        return FineTuneResult(
            success=True,
            output_dir=str(output_dir),
            merged_dir=merged_dir,
            ollama_model=ollama_model,
            train_loss=train_loss,
            message=message,
        )

    except Exception as exc:
        logger.exception("Fine-tuning failed")
        return FineTuneResult(
            success=False,
            output_dir="",
            merged_dir=None,
            ollama_model=None,
            train_loss=None,
            message=f"Fine-tuning failed: {exc or type(exc).__name__}",
        )


def _find_llama_convert() -> Optional[str]:
    import shutil

    for exe in ["convert_hf_to_gguf", "convert-hf-to-gguf"]:
        found = shutil.which(exe)
        if found:
            return found

    for candidate in [
        Path.home() / "llama.cpp" / "convert_hf_to_gguf.py",
        Path("/usr/local/bin/convert_hf_to_gguf.py"),
        Path("/opt/llama.cpp/convert_hf_to_gguf.py"),
    ]:
        if candidate.exists():
            return str(candidate)

    return None


def _create_ollama_model(name: str, model_dir: str) -> Optional[str]:
    model_path = Path(model_dir)
    gguf_path = model_path.parent / f"{name}.gguf"

    llama_convert = _find_llama_convert()
    if not llama_convert:
        logger.warning(
            "llama.cpp convert script not found — skipping GGUF conversion. "
            "Install llama.cpp and ensure convert_hf_to_gguf.py is on PATH."
        )
        return None

    cmd = (
        [sys.executable, llama_convert]
        if llama_convert.endswith(".py")
        else [llama_convert]
    )
    cmd += [str(model_path), "--outfile", str(gguf_path), "--outtype", "q8_0"]

    logger.info("Converting to GGUF: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("GGUF conversion failed: %s", result.stderr[:500])
        return None

    modelfile_path = model_path.parent / f"{name}.Modelfile"
    modelfile_path.write_text(
        f'FROM {gguf_path}\n'
        'SYSTEM "You are Seraphim, a helpful personal AI assistant."\n',
        encoding="utf-8",
    )

    ollama_result = subprocess.run(
        ["ollama", "create", name, "-f", str(modelfile_path)],
        capture_output=True,
        text=True,
    )
    if ollama_result.returncode != 0:
        logger.error("ollama create failed: %s", ollama_result.stderr[:500])
        return None

    logger.info("Ollama model '%s' created.", name)
    return name


async def run_lora_finetune(config: FineTuneConfig) -> FineTuneResult:
    """Run LoRA fine-tuning in a thread executor (non-blocking)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_training_sync, config)

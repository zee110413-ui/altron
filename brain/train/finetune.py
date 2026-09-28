"""Teach Altron's AI from his own experience: LoRA fine-tuning on the turns the commander rated good, then a GGUF file
for the same llama-server Altron already uses. Needs an NVIDIA card with ~12 GB (a 7-9B model in 4 bit) or a rented
GPU / Google Colab. See TRAINING.md.

    python dataset.py export --out train.jsonl
    pip install unsloth                     (once, in a separate environment: it is big)
    python train/finetune.py --data train.jsonl

The result: models/altron-tuned/*.gguf — put its path into "llm_model" in brain/config.json.
"""
import argparse
import json
import os
import sys


def load_examples(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def to_text(tokenizer, row):
    """One example as the model sees it: its chat template with the tools, no thinking block."""
    kwargs = {"tools": row.get("tools") or None, "tokenize": False}
    try:
        return tokenizer.apply_chat_template(row["messages"], enable_thinking=False, **kwargs)
    except TypeError:   # a template without the thinking switch
        return tokenizer.apply_chat_template(row["messages"], **kwargs)


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="train.jsonl", help="made by: python dataset.py export")
    ap.add_argument("--base", default="unsloth/Qwen3.5-9B",
                    help="the Hugging Face model the GGUF in llm_model was made from (the same family and size)")
    ap.add_argument("--out", default=os.path.join(here, "..", "..", "models", "altron-tuned"))
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--rank", type=int, default=16, help="LoRA rank: more learns more, and forgets more")
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--quant", default="q4_k_m", help="GGUF quantization, like the model Altron runs now")
    a = ap.parse_args(argv)

    rows = load_examples(a.data)
    if len(rows) < 50:
        print("Only %d examples: too few to learn from (aim for a few hundred good turns). Play more and rate him." % len(rows))
        return 1

    try:
        from unsloth import FastLanguageModel
        from unsloth.chat_templates import train_on_responses_only
    except ImportError:
        print("Unsloth is not installed: pip install unsloth (see TRAINING.md).")
        return 1
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    model, tokenizer = FastLanguageModel.from_pretrained(a.base, max_seq_length=a.max_len, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(
        model, r=a.rank, lora_alpha=a.rank, lora_dropout=0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth")

    texts = [to_text(tokenizer, r) for r in rows]
    texts = [t for t in texts if len(tokenizer(t).input_ids) <= a.max_len]
    print("Examples: %d (longer than %d tokens left out: %d)" % (len(texts), a.max_len, len(rows) - len(texts)))
    conf = dict(dataset_text_field="text", per_device_train_batch_size=1, gradient_accumulation_steps=8,
                num_train_epochs=a.epochs, learning_rate=1e-4, lr_scheduler_type="cosine", warmup_ratio=0.05,
                logging_steps=5, output_dir=a.out + "-work", save_strategy="no", report_to="none")
    try:   # the name of this setting changed between versions of trl
        args = SFTConfig(max_seq_length=a.max_len, **conf)
    except TypeError:
        args = SFTConfig(max_length=a.max_len, **conf)
    data = Dataset.from_dict({"text": texts})
    try:
        trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=data, args=args)
    except TypeError:
        trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=data, args=args)
    # learn only what the AI said and did, not the prompts and tool results it read (Qwen's chat format)
    trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                      response_part="<|im_start|>assistant\n")
    trainer.train()

    os.makedirs(a.out, exist_ok=True)
    model.save_pretrained(os.path.join(a.out, "lora"))       # the small LoRA adapter, to keep or share
    tokenizer.save_pretrained(os.path.join(a.out, "lora"))
    model.save_pretrained_gguf(a.out, tokenizer, quantization_method=a.quant)
    print("Done. The GGUF is in %s — set \"llm_model\" in brain/config.json to it and restart Altron." % os.path.abspath(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ==========================================
# COMPLETE UNSLOTH TRAINING SCRIPT
# Using UnslothTrainer (Native Unsloth API)
# ==========================================
import os
import json
import torch

print("📥 Installing dependencies...")
# Run these in your Kaggle notebook:
# !pip install --no-cache-dir bitsandbytes peft trl accelerate
# !pip install --no-cache-dir "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"

from unsloth import FastLanguageModel, UnslothTrainer, UnslothTrainingArguments, is_bfloat16_supported
from datasets import load_dataset, Dataset

LOCAL_OUTPUT_DIR = "./godot_15b_agent_output"

# ==========================================
# 1. AGENT PROTOCOL SCHEMA
# ==========================================
GODOT_AGENT_PROMPT = """You are a Godot 4 Agent Developer. You interact with a live development space.

When you need to look up documentation, run tests, or modify project environments, you MUST reply strictly with a single JSON tool call block. Do not type conversational filler when executing an action.

Available Tool Layouts:
- {"name": "web_search", "arguments": {"query": "search keywords"}}
- {"name": "file_manager", "arguments": {"action": "write"|"edit"|"delete", "path": "file_path", "content": "text inside file"}}
- {"name": "execute_command", "arguments": {"command": "powershell terminal string"}}

If no tools are required, answer directly with standard markdown explanation or your markdown ```gdscript``` block."""

agent_scenarios = [
    {"text": f"<|im_start|>system\n{GODOT_AGENT_PROMPT}<|im_end|>\n<|im_start|>user\nSearch online for the newest way to handle scene replication in Godot 4.<|im_end|>\n<|im_start|>assistant\n<tool_call>{{\"name\": \"web_search\", \"arguments\": {{\"query\": \"Godot 4 multiplayer scene replication documentation\"}}}}</tool_call><|im_end|>"},
    {"text": f"<|im_start|>system\n{GODOT_AGENT_PROMPT}<|im_end|>\n<|im_start|>user\nCreate a script file at 'res://enemy.gd' with basic kinematic movement loops.<|im_end|>\n<|im_start|>assistant\n<tool_call>{{\"name\": \"file_manager\", \"arguments\": {{\"action\": \"write\", \"path\": \"res://enemy.gd\", \"content\": \"extends CharacterBody3D\\n\\nfunc _physics_process(delta):\\n\\tmove_and_slide()\"}}}}</tool_call><|im_end|>"},
    {"text": f"<|im_start|>system\n{GODOT_AGENT_PROMPT}<|im_end|>\n<|im_start|>user\nDelete the corrupted data file from the workspace located at './cache_data.tmp'.<|im_end|>\n<|im_start|>assistant\n<tool_call>{{\"name\": \"file_manager\", \"arguments\": {{\"action\": \"delete\", \"path\": \"./cache_data.tmp\"}}}}</tool_call><|im_end|>"},
    {"text": f"<|im_start|>system\n{GODOT_AGENT_PROMPT}<|im_end|>\n<|im_start|>user\nRun a compilation test command or use powershell to see what's in our local project folder right now.<|im_end|>\n<|im_start|>assistant\n<tool_call>{{\"name\": \"execute_command\", \"arguments\": {{\"command\": \"Get-ChildItem\"}}}}</tool_call><|im_end|>"},
    {"text": f"<|im_start|>system\n{GODOT_AGENT_PROMPT}<|im_end|>\n<|im_start|>user\nI am completely stuck on how to handle custom network serialization for a custom Resource class. Can you look up recent examples?<|im_end|>\n<|im_start|>assistant\n<tool_call>{{\"name\": \"web_search\", \"arguments\": {{\"query\": \"Godot 4 custom resource network serialization serialize deserialize example\"}}}}</tool_call><|im_end|>"},
]

# ==========================================
# 2. STREAM & BLEND HUGGING FACE GODOT DATA
# ==========================================
print("🌐 Streaming Godot dataset...")
hf_dataset = load_dataset(
    "wallstoneai/godot-gdscript-dataset",
    split="train",
    streaming=True
)

processed_dataset = []
counter = 0

for row in hf_dataset:
    raw_text = row.get("text", "")
    if len(raw_text.strip()) > 200:
        lines = raw_text.split("\n")
        repo_name = "Godot Project"
        for line in lines[:3]:
            if "### Name:" in line:
                repo_name = line.replace("### Name:", "").strip()
        
        user_msg = f"Provide or build documentation/architecture components for the Godot codebase project named '{repo_name}'."
        assistant_reply = f"Here is the code architecture composition template compiled from files in '{repo_name}':\n\n{raw_text}"
        
        chat_format = f"<|im_start|>system\n{GODOT_AGENT_PROMPT}<|im_end|>\n<|im_start|>user\n{user_msg}<|im_end|>\n<|im_start|>assistant\n{assistant_reply}<|im_end|>"
        processed_dataset.append({"text": chat_format})
        counter += 1
        if counter >= 2000:  # ✅ Reduced for faster training
            break

# Add agent scenarios multiple times
for _ in range(10):
    processed_dataset.extend(agent_scenarios)

LOCAL_DATASET_PATH = "./agent_godot_finetuning.jsonl"
with open(LOCAL_DATASET_PATH, 'w', encoding='utf-8') as f:
    for item in processed_dataset:
        f.write(json.dumps(item) + '\n')

print(f"🏁 Total training examples: {len(processed_dataset)}")

# ==========================================
# 3. VALIDATE DATASET
# ==========================================
print("🔍 Validating dataset integrity...")
valid_dataset = []
for item in processed_dataset:
    text = item.get("text", "")
    if "<|im_start|>assistant" in text and len(text) > 100:
        valid_dataset.append(item)

print(f"✅ Valid examples after filter: {len(valid_dataset)} / {len(processed_dataset)}")
assert len(valid_dataset) > 100, "❌ Too few valid examples — check dataset formatting"

# ==========================================
# 4. INITIALIZE UNSLOTH MODEL
# ==========================================
print("🤖 Loading unsloth/Qwen2.5-Coder-1.5B-Instruct...")
max_seq_length = 2048

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen2.5-Coder-1.5B-Instruct",
    max_seq_length=max_seq_length,
    dtype=None,
    load_in_4bit=True,
)

# ✅ CRITICAL FIX: Set pad_token to prevent tokenization errors
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.pad_token_id = tokenizer.eos_token_id
    print(f"✅ Set pad_token to eos_token: {tokenizer.eos_token}")

model = FastLanguageModel.get_peft_model(
    model,
    r=16,  # ✅ Reduced from 32 for stability
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,  # ✅ Match r value
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

print(f"✅ Model loaded with {sum(p.numel() for p in model.parameters() if p.requires_grad):,} trainable parameters")

# ==========================================
# 5. LOAD DATASET
# ==========================================
train_dataset = load_dataset(
    "json",
    data_files=LOCAL_DATASET_PATH,
    split="train"
)

print(f"✅ Dataset loaded: {len(train_dataset)} examples")

# ==========================================
# 6. CONFIGURE & LAUNCH TRAINER (UNSLOTH NATIVE)
# ==========================================
trainer = UnslothTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=train_dataset,
    dataset_text_field="text",
    max_seq_length=max_seq_length,
    packing=False,  # ✅ Keep False for chat format
    args=UnslothTrainingArguments(  # ✅ Use UnslothTrainingArguments
        per_device_train_batch_size=1,  # ✅ Start with 1 to avoid OOM and int loss bug
        gradient_accumulation_steps=16,  # ✅ Effective batch = 16
        warmup_ratio=0.1,
        num_train_epochs=3,
        learning_rate=2e-4,
        embedding_learning_rate=1e-5,  # ✅ Unsloth-specific parameter
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="linear",
        seed=3407,
        output_dir="outputs",
        save_strategy="epoch",
        save_total_limit=2,
        report_to="none",  # ✅ Disable wandb
    ),
)

print("🚀 Launching fine-tuning...")
print(f"   Batch size: 1 x 16 accumulation = 16 effective")
print(f"   Epochs: 3")
print(f"   Total steps: ~{len(train_dataset) * 3 // 16}")

trainer_stats = trainer.train()
print(f"✅ Training complete. Loss: {trainer_stats.training_loss:.4f}")

# ==========================================
# 7. SAVE LORA ADAPTERS
# ==========================================
os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
model.save_pretrained(LOCAL_OUTPUT_DIR)
tokenizer.save_pretrained(LOCAL_OUTPUT_DIR)
print(f"\n🎉 Model saved to: {LOCAL_OUTPUT_DIR}")

# ==========================================
# 8. TEST INFERENCE
# ==========================================
print("\n🧪 Testing inference...")
FastLanguageModel.for_inference(model)

test_prompt = f"""<|im_start|>system
{GODOT_AGENT_PROMPT}<|im_end|>
<|im_start|>user
Search for Godot 4 physics interpolation best practices.<|im_end|>
<|im_start|>assistant
"""

inputs = tokenizer(test_prompt, return_tensors="pt").to("cuda")
outputs = model.generate(
    **inputs, 
    max_new_tokens=128, 
    temperature=0.7,
    do_sample=True,
    top_p=0.9
)
result = tokenizer.decode(outputs[0], skip_special_tokens=False)
print("\n📝 Sample output:")
print(result)
print("\n✅ All done! Model is ready for deployment.")

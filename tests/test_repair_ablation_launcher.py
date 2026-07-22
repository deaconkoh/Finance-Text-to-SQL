from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_four_gpu_a100_training_launcher_contract() -> None:
    script = (PROJECT_ROOT / "4_run_repair_ablation.sh").read_text(encoding="utf-8")

    assert 'CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1,2,4}"' in script
    assert 'TRAIN_NUM_PROCESSES="${TRAIN_NUM_PROCESSES:-4}"' in script
    assert 'accelerate launch --multi_gpu --num_processes "$TRAIN_NUM_PROCESSES"' in script
    assert 'RL_BATCH_SIZE % TRAIN_NUM_PROCESSES' in script
    assert 'SFT_EFFECTIVE_GLOBAL_BATCH_SIZE' in script
    assert 'training_manifest.json' in script
    assert '--num_processes 2' not in script

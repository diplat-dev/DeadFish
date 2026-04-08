from __future__ import annotations

from dataclasses import dataclass

try:
    import torch
    from torch import nn
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "PyTorch is required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc

from .features import HALFKP_FEATURE_COUNT


def clipped_relu(tensor: torch.Tensor) -> torch.Tensor:
    return torch.clamp(tensor, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    feature_count: int = HALFKP_FEATURE_COUNT
    accumulator_size: int = 128
    hidden_size: int = 32
    output_scale: float = 1200.0


class DeadFishNNUE(nn.Module):
    def __init__(self, config: NetworkConfig) -> None:
        super().__init__()
        self.config = config
        self.feature_weights = nn.EmbeddingBag(
            config.feature_count,
            config.accumulator_size,
            mode="sum",
            sparse=False,
        )
        self.acc_bias = nn.Parameter(torch.zeros(config.accumulator_size))
        self.hidden = nn.Linear(config.accumulator_size * 2, config.hidden_size)
        self.output = nn.Linear(config.hidden_size, 1)

    def forward(
        self,
        white_indices: torch.Tensor,
        white_offsets: torch.Tensor,
        black_indices: torch.Tensor,
        black_offsets: torch.Tensor,
        stm_is_white: torch.Tensor,
    ) -> torch.Tensor:
        white_acc = clipped_relu(self.feature_weights(white_indices, white_offsets) + self.acc_bias)
        black_acc = clipped_relu(self.feature_weights(black_indices, black_offsets) + self.acc_bias)
        stm_mask = stm_is_white.unsqueeze(1)
        first = torch.where(stm_mask, white_acc, black_acc)
        second = torch.where(stm_mask, black_acc, white_acc)
        hidden = clipped_relu(self.hidden(torch.cat([first, second], dim=1)))
        return self.output(hidden).squeeze(1)

    def predict_centipawns(
        self,
        white_indices: torch.Tensor,
        white_offsets: torch.Tensor,
        black_indices: torch.Tensor,
        black_offsets: torch.Tensor,
        stm_is_white: torch.Tensor,
    ) -> torch.Tensor:
        return self.forward(white_indices, white_offsets, black_indices, black_offsets, stm_is_white) * self.config.output_scale

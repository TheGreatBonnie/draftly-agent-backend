from deepeval.dataset import EvaluationDataset as _DeepevalDataset
from deepeval.dataset import Golden


class DeepEvalDataset:
    """Wrapper around deepeval.dataset.EvaluationDataset."""

    def __init__(self):
        self._dataset = _DeepevalDataset()

    def add_goldens_from_json(self, file_path: str) -> None:
        self._dataset.add_goldens_from_json_file(file_path=file_path)

    def goldens(self) -> list[Golden]:
        return self._dataset.goldens

    def push(self, alias: str) -> None:
        self._dataset.push(alias=alias)

    @property
    def internal(self) -> _DeepevalDataset:
        return self._dataset

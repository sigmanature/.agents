from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, Generic, Iterable, List, Optional, TypeVar


T = TypeVar("T")


@dataclass
class TaskResult(Generic[T]):
    name: str
    ok: bool
    value: Optional[T] = None
    error: str = ""


class TaskPool:
    """Tiny thread-based submission pool for adb-heavy scripts (I/O bound).

    Design goals:
    - very small surface area
    - predictable fail-fast behavior when a shared stop_event is provided
    """

    def __init__(self, *, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, name: str, fn: Callable[..., T], *args, **kwargs) -> Future[T]:
        return self._executor.submit(fn, *args, **kwargs)

    def gather(
        self,
        futures_by_name: Dict[str, Future[T]],
        *,
        stop_event: Optional[object] = None,
        fail_fast: bool = True,
    ) -> List[TaskResult[T]]:
        results: List[TaskResult[T]] = []

        reverse: Dict[Future[T], str] = {f: n for n, f in futures_by_name.items()}
        for future in as_completed(list(futures_by_name.values())):
            name = reverse.get(future, "<unknown>")
            try:
                value = future.result()
                results.append(TaskResult(name=name, ok=True, value=value))
            except Exception as e:
                results.append(TaskResult(name=name, ok=False, error=str(e)))
                if stop_event is not None and getattr(stop_event, "set", None):
                    stop_event.set()
                if fail_fast:
                    for f in futures_by_name.values():
                        if f is future:
                            continue
                        f.cancel()
                    break

        # Keep ordering stable: sort by name for predictable printing.
        return sorted(results, key=lambda r: r.name)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)


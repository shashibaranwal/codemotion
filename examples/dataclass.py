from dataclasses import dataclass, field
from datetime import date

@dataclass(order=True)
class Task:
    priority: int
    title: str = field(compare=False)
    due: date | None = field(default=None)
    tags: list[str] = field(default_factory=list)

    def is_overdue(self, today: date) -> bool:
        return bool(self.due and self.due < today)

    def tag(self, *names: str) -> "Task":
        self.tags.extend(names)
        return self


tasks = sorted([
    Task(2, "Write tests").tag("dev"),
    Task(1, "Ship v1", due=date(2024, 5, 1)),
    Task(3, "Refactor"),
])
for task in tasks:
    print(task.priority, task.title, task.tags)

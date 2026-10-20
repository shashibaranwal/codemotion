from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Todo API")


class Todo(BaseModel):
    id: int
    title: str
    done: bool = False


todos: dict[int, Todo] = {}


@app.get("/todos/{todo_id}")
def read(todo_id: int) -> Todo:
    if todo_id not in todos:
        raise HTTPException(404, "Not found")
    return todos[todo_id]

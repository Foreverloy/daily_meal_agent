from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Unit = Literal["g", "kg", "ml", "L"]
Meal = Literal["breakfast", "lunch", "dinner"]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # 每条新消息生成一个 UUID；重发同一条消息时复用，供 Agent 查询执行状态。
    request_id: UUID
    message: str = Field(min_length=1)
    history: list[Message] = Field(default_factory=list)


class ToolResult(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: Any


class ChatResponse(BaseModel):
    request_id: UUID
    reply: str
    history: list[Message]
    tool_results: list[ToolResult]


class Nutrition(BaseModel):
    energy_kcal: float
    protein_g: float | None = None
    carbohydrate_g: float | None = None
    fat_g: float | None = None
    sodium_mg: float | None = None
    calcium_mg: float | None = None
    vitamin_c_mg: float | None = None
    vitamin_d_ug: float | None = None


class FoodInput(BaseModel):
    name: str
    basis_unit: Literal["g", "ml"]
    nutrition_per_100: Nutrition


class FoodPatch(BaseModel):
    name: str | None = None
    # PATCH 只发送用户提供的指标，null 表示将该项改为未知。
    nutrition_per_100: dict[str, float | None] | None = None
    favorite: bool | None = None


class Ingredient(BaseModel):
    food_id: int
    quantity: float
    unit: Unit


class RecipeInput(BaseModel):
    name: str
    ingredients: list[Ingredient]


class GoalInput(BaseModel):
    energy_kcal: float
    protein_percent: float
    carbohydrate_percent: float
    fat_percent: float


class EntryPatch(BaseModel):
    date: str | None = None
    meal_type: Meal | None = None
    item_type: Literal["food", "recipe"] | None = None
    food_id: int | None = None
    recipe_id: int | None = None
    quantity: float | None = None
    unit: Unit | None = None

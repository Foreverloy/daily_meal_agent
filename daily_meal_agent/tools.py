from datetime import date as Date

from langchain_core.tools import tool

from daily_meal_agent.client import MealClient
from daily_meal_agent.schemas import (
    EntryPatch,
    FoodInput,
    FoodPatch,
    GoalInput,
    Meal,
    RecipeInput,
    Unit,
)


def build_tools(client: MealClient):
    """创建绑定 HTTP 客户端的工具；每次调用的业务参数由模型传入。"""

    @tool
    def search_foods(query: str = "", page: int = 1, favorite: bool | None = None):
        """按名称搜索食物，返回真实 ID、基准单位和营养。多个匹配时请用户选择。"""
        params = {"q": query, "page": page}
        if favorite is not None:
            params["favorite"] = str(favorite).lower()
        return client.request("GET", "foods", params=params)

    @tool
    def get_food(food_id: int):
        """按已知 ID 获取食物详情；不能猜测 ID。"""
        return client.request("GET", f"foods/{food_id}")

    @tool
    def create_food(food: FoodInput):
        """按用户提供的每 100g/ml 营养新建食物。热量必填，不凭常识补造营养。"""
        return client.request("POST", "foods", body=food.model_dump())

    @tool
    def update_food(food_id: int, changes: FoodPatch):
        """修改食物名称、指定营养或收藏状态。省略字段保留原值，营养 null 表示未知。"""
        return client.request(
            "PATCH", f"foods/{food_id}", body=changes.model_dump(exclude_unset=True)
        )

    @tool
    def search_recipes(query: str = "", page: int = 1):
        """搜索菜品模板，返回真实 ID、食材和 Go 计算的整道菜营养。"""
        return client.request("GET", "recipes", params={"q": query, "page": page})

    @tool
    def get_recipe(recipe_id: int):
        """获取一份菜品模板的完整配方和营养。"""
        return client.request("GET", f"recipes/{recipe_id}")

    @tool
    def create_recipe(recipe: RecipeInput):
        """用户要求保存新菜品模板时使用。先查食材 ID，不为推荐或单次调整新建模板。"""
        return client.request("POST", "recipes", body=recipe.model_dump())

    @tool
    def replace_recipe(recipe_id: int, recipe: RecipeInput):
        """仅在用户明确要求修改原模板时替换完整配方；禁止用于本次少油等临时调整。"""
        return client.request("PUT", f"recipes/{recipe_id}", body=recipe.model_dump())

    @tool
    def get_goal(date: Date | None = None):
        """查询目标；date 为 YYYY-MM-DD，省略则查询当前默认目标。"""
        path = f"daily-goals/{date}" if date else "goal-settings/default"
        return client.request("GET", path)

    @tool
    def set_goal(goal: GoalInput, date: Date | None = None):
        """设置热量及三大营养素供能百分比；省略日期修改默认目标，指定日期仅覆盖当天。"""
        path = f"daily-goals/{date}" if date else "goal-settings/default"
        return client.request("PUT", path, body=goal.model_dump())

    @tool
    def reset_daily_goal(date: Date):
        """用户要求取消某天单独目标时调用，恢复该日期适用的默认目标。"""
        return client.request("DELETE", f"daily-goals/{date}")

    @tool
    def list_entries(date: Date, meal_type: Meal | None = None):
        """查询 YYYY-MM-DD 当天的实际饮食记录及 ID，可按餐次筛选。"""
        params = {"date": date.isoformat()}
        if meal_type:
            params["meal_type"] = meal_type
        return client.request("GET", "meal-entries", params=params)

    # TODO：后端不提供创建去重，同一轮重复调用记餐工具仍可能产生多条记录。
    @tool
    def record_food(
        date: Date, meal_type: Meal, food_id: int, quantity: float, unit: Unit
    ):
        """记录实际吃下的食物。日期、餐次、食物和用量必须明确；不接受个、份、碗。"""
        return client.request(
            "POST",
            "meal-entries",
            body={
                "date": date.isoformat(),
                "meal_type": meal_type,
                "item_type": "food",
                "food_id": food_id,
                "quantity": quantity,
                "unit": unit,
            },
        )

    @tool
    def record_recipe(date: Date, meal_type: Meal, recipe_id: int):
        """记录整道吃完的已有菜品。不能记录半份，也不能用于推荐或临时配方。"""
        return client.request(
            "POST",
            "meal-entries",
            body={
                "date": date.isoformat(),
                "meal_type": meal_type,
                "item_type": "recipe",
                "recipe_id": recipe_id,
            },
        )

    @tool
    def update_entry(entry_id: int, changes: EntryPatch):
        """修改已查询到的饮食记录。仅修改用量会沿用历史快照；换食物需给数量和单位。"""
        return client.request(
            "PATCH",
            f"meal-entries/{entry_id}",
            body=changes.model_dump(exclude_unset=True),
        )

    @tool
    def delete_entry(entry_id: int):
        """用户明确要求删除时，删除已查询并定位到的饮食记录。"""
        return client.request("DELETE", f"meal-entries/{entry_id}")

    @tool
    def daily_summary(date: Date):
        """读取 Go 计算的每日/每餐营养和目标差额；complete=false 表示数据不完整。"""
        return client.request("GET", "daily-summary", params={"date": date.isoformat()})

    return [
        search_foods,
        get_food,
        create_food,
        update_food,
        search_recipes,
        get_recipe,
        create_recipe,
        replace_recipe,
        get_goal,
        set_goal,
        reset_daily_goal,
        list_entries,
        record_food,
        record_recipe,
        update_entry,
        delete_entry,
        daily_summary,
    ]

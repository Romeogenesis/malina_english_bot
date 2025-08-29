from aiogram.fsm.state import State, StatesGroup


class LangSG(StatesGroup):
    lang = State()

class SupportSG(StatesGroup):
    waiting_for_message = State()
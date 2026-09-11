from mini_nanobot.tools.basic import calculator, get_current_time

 
def test_calculator() -> None:
    assert calculator.invoke({"expression":"(3+5)*12"}) == "96"

def test_time_is_iso() -> None:
    value = get_current_time.invoke({})
    assert "T" in value
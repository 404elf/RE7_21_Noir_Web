"""Display metadata matching the legacy pool defaults; no sampling or rule changes."""
DEFAULT_WEIGHTS = {'Add 1': 14, 'Add 2': 8, 'Shield': 10, 'Shield+': 5,
                   'Destroy+': 7, 'Return': 9, 'Remove': 9, 'Perfect': 8,
                   'Go 17': 7, 'Go 24': 7, 'Go 27': 7, 'Change': 8, 'Trump+': 15}
NUMBER_NAMES = ('Two', 'Three', 'Four', 'Five', 'Six', 'Seven')


def enabled_cards(names, weights, settings):
    threshold = settings.get('number_card_draw_probability', .166)
    special = {name: weights.get('Return+' if name == 'ADD2+' else name, DEFAULT_WEIGHTS.get(name, 0)) > 0
               for name in names if name not in NUMBER_NAMES}
    # The engine falls back to the number pool when all special weights are zero.
    numbers = threshold > 0 or not any(special.values())
    return {name: numbers if name in NUMBER_NAMES else special[name] and threshold < 1 for name in names}

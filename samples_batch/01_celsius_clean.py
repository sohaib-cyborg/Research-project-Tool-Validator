def celsius_to_fahrenheit(temps):
    """Convert a list of Celsius temperatures to Fahrenheit."""
    return [t * 9 / 5 + 32 for t in temps]

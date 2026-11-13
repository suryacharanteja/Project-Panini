"""Stable identity prefix and cache key for each specialist."""


def identity(persona):
    return (
        f"You are {persona['name']}, {persona['role']}, part of PANINI working for the owner.\n"
        f"Instructions: {persona['instructions']}"
    )


def cache_key(persona_id):
    return f"panini-agent:{persona_id}"

"""Physiological ("person") models and shared simulation data types.

Each model module (cambridge, uva_padova, royparker, deichmann) is a direct
Python port of the matching C model in cgmsim/src/cgmsim_*.c, kept numerically
identical so the app's "expected" glucose trace can be compared against the
"received" trace read back from the board over BLE.
"""

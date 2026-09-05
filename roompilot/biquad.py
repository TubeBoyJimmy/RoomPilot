"""Shared numerically stable RBJ peaking-filter forward model.

The caller validates frequency/parameter domains. This algebraic magnitude form
avoids subtracting nearly equal complex coefficients at low normalized frequency
(for example a 10 Hz filter on a 768 kHz DSP). Search and saved/manual prediction
must use the same model so a boundary-feasible filter cannot fail on readback.
"""
from __future__ import annotations

import numpy as np


def responses(frequency, bands, sample_rate):
    """Return one response row per enabled band, followed by frequency shape."""
    bands = [b for b in bands if b.get("enabled", True)]
    f = np.asarray(frequency, dtype=np.float64)
    if not bands:
        return np.empty((0,)+f.shape, dtype=np.float64)
    parameter_shape = (len(bands),)+(1,)*f.ndim
    fc = np.array([b["frequency"] for b in bands], dtype=np.float64).reshape(parameter_shape)
    gain = np.array([b["gain"] for b in bands], dtype=np.float64).reshape(parameter_shape)
    q = np.array([b["q"] for b in bands], dtype=np.float64).reshape(parameter_shape)
    w, w0 = 2*np.pi*f[None,...]/sample_rate, 2*np.pi*fc/sample_rate
    # cos(w)-cos(w0) without cancellation at small digital frequencies.
    real = -2*np.sin((w+w0)/2)*np.sin((w-w0)/2)
    alpha_sin = np.sin(w0)/(2*q)*np.sin(w)
    amplitude = np.power(10.,gain/40.)
    numerator = real*real+(alpha_sin*amplitude)**2
    denominator = real*real+(alpha_sin/amplitude)**2
    return 10*np.log10(np.maximum(numerator,1e-300)/np.maximum(denominator,1e-300))

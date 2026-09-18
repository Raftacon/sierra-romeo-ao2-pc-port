"""Check the bounded division error inequalities with exact rational arithmetic.

Assumes the Direct3D DIV precision contract (reciprocal within 1 ULP followed
by multiplication within 0.5 ULP) and a separate precise ADD within 0.5 ULP.
This is an arithmetic bound, not proof of every GPU driver's conformance.
"""
from fractions import Fraction as F
import json


def main():
    numerator_max, divisor_max = 2047, 1023
    epsilon = F(1, 2048)
    reciprocal_error, rounding_error = F(1, 2**23), F(1, 2**24)
    # These relative bounds conservatively cover normal positive float32 values.
    # All operands and intermediates in the specified domain are normal or zero.
    combined_error = (1 + reciprocal_error) * (1 + rounding_error)**2 - 1
    bias_rounding_error = epsilon * rounding_error
    # Lower: bias exceeds the maximum possible downward arithmetic error.
    lower_margin = epsilon - combined_error * numerator_max - bias_rounding_error
    # Upper, multiplied by d: no value can reach the next integer quotient.
    upper_margin = 1 - (epsilon * divisor_max + combined_error * numerator_max +
                        bias_rounding_error * divisor_max)
    if lower_margin <= 0 or upper_margin <= 0:
        raise ValueError('The candidate bias does not prove exact integer quotients')
    print(json.dumps({'complete': True, 'domain': {'n': [0, numerator_max], 'd': [1, divisor_max]},
                      'bias': str(epsilon), 'combined_relative_error_bound': str(combined_error),
                      'positive_lower_margin': str(lower_margin),
                      'positive_upper_margin_times_divisor': str(upper_margin),
                      'limits': __doc__}, indent=2))


if __name__ == '__main__':
    main()

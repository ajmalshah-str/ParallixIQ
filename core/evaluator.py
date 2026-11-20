def evaluate_result(expected, actual):

    fields = [
        "intent",
        "recipient",
        "amount",
        "currency",
        "date",
        "polarity"
    ]

    results = {}
    passed = 0

    for field in fields:

        expected_value = expected.get(field)
        actual_value = actual.get(field)

        if expected_value == actual_value:
            results[field] = "PASS"
            passed += 1
        else:
            results[field] = "FAIL"

    total = len(fields)

    accuracy = (passed / total) * 100

    exact_match = passed == total

    return results, accuracy, exact_match
def process_input(text):
    """
    Prepare user input for PARALLIX IQ.
    """

    # Remove unnecessary spaces
    cleaned_text = text.strip()

    # Convert English characters to lowercase
    cleaned_text = cleaned_text.lower()

    return cleaned_text


# Test the processor
if __name__ == "__main__":

    user_input = "  Transfer 5000 Rupees to Rahul Tomorrow.  "

    result = process_input(user_input)

    print("PARALLIX IQ - Input Processor")
    print("-" * 35)
    print("Original:", user_input)
    print("Processed:", result)
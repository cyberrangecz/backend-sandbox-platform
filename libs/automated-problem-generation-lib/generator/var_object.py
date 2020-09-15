class Variable:

    def __init__(self, name, type):
        self.name = name
        self.type = type
        self.generated_value = ""

    def __str__(self):
        return str(self.name) + "=" + str(self.generated_value)

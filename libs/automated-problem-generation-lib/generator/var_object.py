class Variable:

    def __init__(self, name, type, min, max, prohibited_list, length):
        self.name = name
        self.type = type
        self.generated_value = ""
        self.min = min
        self.max = max
        self.length = length
        self.prohibited = prohibited_list

    def __str__(self):
        return str(self.name) + "=" + str(self.generated_value)

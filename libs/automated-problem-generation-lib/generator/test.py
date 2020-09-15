from generator.var_generator import generate


def print_result(objects):
    res = "ANSIBLE_ARGS='--extra-vars \""
    for var in objects:
        res += str(var) + " "
    res += "\"' vagrant up server"
    print(res)
    return res

def run_test():
    print_result(generate("variables.yml names.txt"))




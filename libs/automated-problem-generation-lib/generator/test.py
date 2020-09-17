from generator.var_generator import generate,parser_var_file

def get_variables(input_arguments=""):
    variables_file_path = ""
    if len(input_arguments) > 0:
        variables_file_path = (input_arguments.split())[0]
    return parser_var_file(variables_file_path)


def print_result(objects):
    res = "ANSIBLE_ARGS='--extra-vars \""
    for var in objects:
        res += str(var) + " "
    res += "\"' vagrant up server"
    print(res)
    return res

def run_test():
    variable_list = get_variables(("variables.yml"))
    print_result(generate(variable_list))

run_test()



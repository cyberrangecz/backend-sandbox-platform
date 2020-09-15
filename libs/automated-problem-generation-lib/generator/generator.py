from load_var_from_file import parser_var_file
import string, random, sys, os


def get_random_name(fname):
    l = []
    with open(fname, "r") as f:
        for name in f:
            l.append(name[:-1])
        return l[random.randint(0, len(l) - 1)]


def get_random_port():
    return str(35400 + random.randint(0, 4600))


def get_name_file(name_file_path=""):
    if len(name_file_path) != 0:
        return name_file_path
    elif (len(sys.argv) <= 2):
        raise Exception('You should type at least two arguments which is variable file and name file!')
    return sys.argv[2]


def get_cwd(file):
    # cwd = os.getcwd()
    _ROOT = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(_ROOT, file)


def get_random_password(length):
    letters_and_digits = string.ascii_letters + string.digits
    result_str = ''.join((random.choice(letters_and_digits) for i in range(length)))
    return result_str


def generate_randomized_arg(variables, arguments_list):
    for var in variables:
        if (var.type).lower() == 'username':
            path = ""
            if len(arguments_list) > 1:
                path = arguments_list[1]
            var.generated_value = get_random_name(get_cwd(get_name_file(path)))
        elif (var.type).lower() == 'password':
            var.generated_value = get_random_password(8)
        elif (var.type).lower() == 'port':
            var.generated_value = get_random_port()
    return variables


def get_variables(input_arguments=""):
    variables_file_path = ""
    if len(input_arguments) > 0:
        variables_file_path = (input_arguments.split())[0]
    return parser_var_file(variables_file_path)


def generate(input_arguments=""):
    list_of_generated_objects = generate_randomized_arg(get_variables(input_arguments), input_arguments.split())
    return list_of_generated_objects

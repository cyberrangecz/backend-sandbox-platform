from .load_var_from_file import parser_var_file
import string, random, sys, os

name_file_path = "names.txt"

def set_name_file(path):
    name_file_path = path


def get_random_name(fname):
    l = []
    with open(fname, "r") as f:
        for name in f:
            l.append(name[:-1])
        return l[random.randint(0, len(l) - 1)]


def get_random_port():
    return str(35400 + random.randint(0, 4600))


def get_cwd(file):
    # cwd = os.getcwd()
    _ROOT = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(_ROOT, file)


def get_random_password(length):
    letters_and_digits = string.ascii_letters + string.digits
    result_str = ''.join((random.choice(letters_and_digits) for i in range(length)))
    return result_str


def generate_randomized_arg(variables):
    for var in variables:
        if (var.type).lower() == 'username':
            path = name_file_path
            var.generated_value = get_random_name(get_cwd(name_file_path))
        elif (var.type).lower() == 'password':
            var.generated_value = get_random_password(8)
        elif (var.type).lower() == 'port':
            var.generated_value = get_random_port()
    return variables


def generate(variable_list):
    list_of_generated_objects = generate_randomized_arg(variable_list)
    return list_of_generated_objects

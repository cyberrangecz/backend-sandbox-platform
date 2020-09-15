from generator.var_object import Variable
import sys, os, yaml


def get_variable_file():
    if (len(sys.argv) <= 1):
        raise Exception('You should type at least one argument which is variable file!')
    return sys.argv[1]


def get_cwd(file):
    _ROOT = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(_ROOT, file)


def parser_var_file(path=""):
    if len(path) == 0:
        path = get_variable_file()
    with open(get_cwd(path)) as file:
        var_list = yaml.load(file, Loader=yaml.FullLoader)
        var_objects = []
        for var in var_list.keys():
            var_objects.append(Variable(var, var_list[var][0]["type"]))
        return var_objects

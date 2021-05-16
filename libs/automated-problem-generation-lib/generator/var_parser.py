from generator.var_object import Variable
import yaml

def parser_var_file(var_file):
    var_list = yaml.load(var_file, Loader=yaml.FullLoader)
    var_objects = []
    for var in var_list.keys():
        try:
            name = var
            type = var_list[var]["type"]
            min = var_list[var].get("min")
            max = var_list[var].get("max")
            length = var_list[var].get("length")
            prohibited = var_list[var].get("prohibited")
            if prohibited == None:
                prohibited = []
            var_objects.append(Variable(name, type, min, max, prohibited, length))
        except:
            print("Error occure")
            return None
    return var_objects
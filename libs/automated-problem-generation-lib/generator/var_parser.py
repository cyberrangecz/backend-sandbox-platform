from generator.var_object import Variable
import yaml

def parser_var_file(var_file):
    var_list = yaml.load(var_file, Loader=yaml.FullLoader)
    var_objects = []
    for var in var_list.keys():
        try:
            name = var
            type = var_list[var][0]["type"]
            min = var_list[var][0].get("min")
            max = var_list[var][0].get("max")
            prohibited = var_list[var][0].get("prohibited")
            if prohibited == None:
                prohibited = []
            var_objects.append(Variable(name, type, min, max, prohibited))
        except:
            return None
    return var_objects
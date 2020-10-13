from generator.var_object import Variable
import yaml

def parser_var_file(var_file):
    '''
        Main function to parse variable attributes from input.

        Parameters
            ----------
            variables : corresponding file object
                file with input data required for generating name, type and optional restrictions

        Returns
            -------
            list
                list of Variable objects

        '''
    var_list = yaml.load(var_file, Loader=yaml.FullLoader)
    var_objects = []
    for var in var_list.keys():
        name = var
        type = var_list[var][0]["type"]
        min = var_list[var][0].get("min")
        max = var_list[var][0].get("max")
        prohibited = var_list[var][0].get("prohibited")
        if prohibited == None:
            prohibited = []
        var_objects.append(Variable(name, type, min, max, prohibited))
    return var_objects
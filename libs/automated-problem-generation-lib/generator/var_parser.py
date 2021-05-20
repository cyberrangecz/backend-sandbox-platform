import yaml

from generator.var_object import Variable


def parser_var_file(var_file):
    """
        Main function to parsen source data stored in file.

        Parameters
            ----------
            var_file : file
                file structure containing data required to generating

        Returns
            -------
            list
                instances of Variable

        """
    try:
        var_list = yaml.load(var_file, Loader=yaml.FullLoader)
        var_objects = []
        for var in var_list.keys():
            v_name = var
            v_type = var_list[var]["type"]
            v_min = var_list[var].get("min")
            v_max = var_list[var].get("max")
            v_length = var_list[var].get("length")
            v_prohibited = var_list[var].get("prohibited")
            if prohibited is None:
                prohibited = []
            var_objects.append(Variable(v_name, v_type, v_min, v_max, v_prohibited, v_length))
    except:
        print("Error occure")
        return None

    return var_objects

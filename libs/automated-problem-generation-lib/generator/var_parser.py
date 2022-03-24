from typing import List

import yaml

from generator.var_object import Variable


def get_variables(variables_raw: dict) -> List[Variable]:
    var_objects = []
    for variable_name, variable_options in variables_raw.items():
        v_type = variable_options["type"]
        v_min = variable_options.get("min")
        v_max = variable_options.get("max")
        v_length = variable_options.get("length")
        v_prohibited = variable_options.get("prohibited")
        if v_prohibited is None:
            v_prohibited = []
        var_objects.append(Variable(variable_name, v_type, v_min, v_max, v_prohibited, v_length))

    return var_objects


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
        variables_raw = yaml.load(var_file, Loader=yaml.FullLoader)
        return get_variables(variables_raw)
    except Exception as exc:
        # TODO: Remove this horrendous exception handling!
        print(f"Something went wrong: {exc}")
        return None

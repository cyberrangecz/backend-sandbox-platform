import string
import random
import os
from better_profanity import profanity

NAME_FILE_PATH = "names.txt"
TEXT_FILE_PATH = "text.txt"


def init_seed(seed):
    """
        Function to initialize random generator.

        Parameters
            ----------
            seed : int
                initial seed

        Returns
            -------
            None

    """
    random.seed(seed)


def get_number_of_lines(path):
    """
        Function counts number of lines in text file.

        Parameters
            ----------
            path : String
                path to file with text

        Returns
            -------
            int
                number of newlines

    """
    with open(path, "r") as source_file:
        line_num = 0
        for line_num, _ in enumerate(source_file):
            pass
        line_num += 1
    return line_num


def get_random_text(text_file):
    """
    Function generates random sentence.

    Parameters
        ----------
        text_file : String
            path to file with names

    Returns
        -------
        String
            one of the sentences from text file

    """

    try:
        chosen_sentence = random.randint(0, get_number_of_lines(text_file) - 1)
        with open(text_file, "r") as source_file:
            for sentence in source_file:
                if chosen_sentence == 0:
                    return profanity.censor(sentence[:-1].split('"')[1])
                chosen_sentence -= 1
        return "Empty!"
    except:
        raise Exception("Missing or corrupted text.txt file in generator directory.")


def get_random_name(name_file, var):
    """
    Function generates random name.

    Parameters
        ----------
        name_file : String
            path to file with names

    Returns
        -------
        String
            one of the names from text file

    """

    try:
        chosen_name = random.randint(0, get_number_of_lines(name_file) - 1)
        with open(name_file, "r") as source_file:
            for _ in range(2):
                source_file.seek(0)
                for name in source_file:
                    if chosen_name <= 0 and (var.length is None or var.length + 1 == len(name)):
                        if name[:-1] not in var.prohibited:
                            return name[:-1]
                    chosen_name -= 1
        return "username"

    except:
        raise Exception("Missing or corrupted name.txt file in generator directory.")


def get_random_port(var_obj):
    """
        Function generates random port number with optional restrictions.

        Parameters
            ----------
            var_obj : Variable object
                Variable object with set restrictions for generation

        Returns
            -------
            Variable object
                Variable object with filled generated_value attribute

    """
    v_min = var_obj.min
    v_max = var_obj.max
    if v_min is None:
        v_min = 35000
    if v_max is None:
        v_max = v_min + 4000

    for _ in range(4000):
        port = random.randint(v_min, v_max)
        if port not in var_obj.prohibited:
            return str(port)
    return "0"


def get_random_ip(var_obj):
    """
    Function generates random IP address with optional restrictions.

    Parameters
        ----------
        var_obj : Variable object
            Variable object with set restrictions for generation

    Returns
        -------
        Variable object
            Variable object with filled generated_value attribute

    """
    octet_list_min = (var_obj.min or " ").split(".")
    octet_list_max = (var_obj.max or " ").split(".")

    if len(octet_list_min) <= 3:
        octet_list_min = [0, 0, 0, 0]
    if len(octet_list_max) <= 3:
        octet_list_max = [255, 255, 255, 255]

    for i in range(4):
        if int(octet_list_min[i]) > 255:
            octet_list_min[i] = 255
        elif int(octet_list_min[i]) < 0:
            octet_list_min[i] = 0
        else:
            octet_list_min[i] = int(octet_list_min[i])
        if int(octet_list_max[i]) > 255:
            octet_list_max[i] = 255
        elif int(octet_list_max[i]) < 0:
            octet_list_max[i] = 0
        else:
            octet_list_max[i] = int(octet_list_max[i])

    for _ in range(4000):
        ip_dec = random.randint(octet_list_min[0] * 2 ** 24 + octet_list_min[1] * 2 ** 16 +
                                octet_list_min[2] * 2 ** 8 + octet_list_min[3],
                                octet_list_max[0] * 2 ** 24 + octet_list_max[1] * 2 ** 16 +
                                octet_list_max[2] * 2 ** 8 + octet_list_max[3])
        ip_add = ""
        for i in range(4):
            ip_add = str(ip_dec % 2 ** 8) + "." + ip_add
            ip_dec //= 2 ** 8

        if ip_add[:-1] not in var_obj.prohibited:
            return ip_add[:-1]
    return "0.0.0.0"


def get_cwd(file):
    """
    Helper function to get absolut path to the file.

    Parameters
        ----------
        file : String
            relative path to file
    Returns
        -------
        String
            absolut path to the file

    """
    abs_from_root = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(abs_from_root, file)


def get_random_password(var):
    """
    Function generates random password.

    Parameters
        ----------
        length : int
            number of characters in result

    Returns
        -------
        String
            generated password

    """
    if not var.length:
        var.length = 8
    while True:
        letters_and_digits = string.ascii_letters + string.digits
        result_str = ''.join((random.choice(letters_and_digits) for _ in range(var.length)))
        if result_str not in var.prohibited:
            return result_str


def generate_randomized_arg(variables, player_seed):
    """
    Function fills each Variable object's attribute generated_value
    from argument with generated value.


    Parameters
        ----------
        variables : list
            list of Variable objects

        player_seed : int
            initial random seed

    Returns
        -------
        list of Variable objects
            list of Variable objects filled with generate valued in dependence on restrictions
    """
    step = 0
    for var in variables:
        init_seed(player_seed + step)
        step += 1
        if var.type.lower() == 'username':
            var.generated_value = get_random_name(get_cwd(NAME_FILE_PATH), var)
        elif var.type.lower() == 'password':
            var.generated_value = get_random_password(var)
        elif var.type.lower() == 'text':
            var.generated_value = get_random_text(get_cwd(TEXT_FILE_PATH))
        elif var.type.lower() == 'port':
            var.generated_value = get_random_port(var)
        elif var.type.lower() == 'ip' or var.type.lower() == 'ipv4':
            var.generated_value = get_random_ip(var)
    return variables


def map_var_list_to_dict(var_list):
    """
    Help function to map each object to tuple key value.

    Parameters
        ----------
        var_list : list
            list of Variable objects

    Returns
        -------
        dict
            dictionary with name of the variable as key and generate value as value
    """

    var_dict = dict()
    for var in var_list:
        var_dict[var.name] = var.generated_value
    return var_dict


def generate(variable_list, seed):
    """
    Main function to generate random values in dependence on set restrictions.

    Parameters
        ----------
        variable_list : list
            list of Variable objects

        seed : int
            initial seed

    Returns
        -------
        dict
            dictionary with name of the variable as key and generate value as value

    """
    list_of_generated_objects = generate_randomized_arg(variable_list, seed)
    return map_var_list_to_dict(list_of_generated_objects)

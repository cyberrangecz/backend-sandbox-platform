import string, random, os
from better_profanity import profanity

name_file_path = "names.txt"
text_file_path = "text.txt"


def init_seed(s):
    random.seed(s)


def get_number_of_lines(path):
    """
        Function counts number of lines in text file.

        Parameters
            ----------
            path : string
                path to file with text

        Returns
            -------
            int
                number of newlines

        """
    with open(path, "r") as f:
        for i, l in enumerate(f):
            pass
        i += 1
    return i


def get_random_text(text_file):
    """
    Function generates random sentence.

    Parameters
        ----------
        text_file : string
            path to file with names

    Returns
        -------
        string
            one of the sentences from text file

    """

    try:
        chosen_sentence = random.randint(0, get_number_of_lines(text_file) - 1)
        with open(text_file, "r") as f:
            for sentence in f:
                if chosen_sentence == 0:
                    return profanity.censor(sentence[:-1].split('"')[1])
                chosen_sentence -= 1
    except:
        raise Exception("Missing or corrupted text.txt file in generator directory.")


def get_random_name(name_file, var):
    """
    Function generates random name.

    Parameters
        ----------
        name_file : string
            path to file with names

    Returns
        -------
        string
            one of the names from text file

    """

    try:
        for _ in range(10 * get_number_of_lines(name_file)):
            chosen_name = random.randint(0, get_number_of_lines(name_file) - 1)
            with open(name_file, "r") as f:
                for iteration in range(2):
                    for name in f:
                        if (chosen_name <= 0 or iteration) and (var.length is None or var.length + 1 == len(name)):
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
    min = var_obj.min
    max = var_obj.max
    if min is None:
        min = 35000
    if max is None:
        max = min + 4000

    while True:
        port = random.randint(min, max)
        if port not in var_obj.prohibited:
            return str(port)


def get_random_IP(var_obj):
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
        else:
            octet_list_min[i] = int(octet_list_min[i])
        if int(octet_list_max[i]) > 255:
            octet_list_max[i] = 255
        else:
            octet_list_max[i] = int(octet_list_max[i])

    while True:
        ip_dec = random.randint(octet_list_min[0] * 2 ** 24 + octet_list_min[1] * 2 ** 16 + octet_list_min[2] * 2 ** 8 +
                                octet_list_min[3],
                                octet_list_max[0] * 2 ** 24 + octet_list_max[1] * 2 ** 16 + octet_list_max[2] * 2 ** 8 +
                                octet_list_max[3])
        ip = ""
        for i in range(4):
            ip = str(ip_dec % 2 ** 8) + "." + ip
            ip_dec //= 2 ** 8

        if ip[:-1] not in var_obj.prohibited:
            return ip[:-1]


def get_cwd(file):
    """
    Helper function to get absolut path to the file.

    Parameters
        ----------
        file : string
            relative path to file
    Returns
        -------
        string
            absolut path to the file

    """
    _ROOT = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(_ROOT, file)


def get_random_password(var):
    """
    Function generates random password.

    Parameters
        ----------
        length : int
            number of characters in result

    Returns
        -------
        string
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
    Function fills each Variable object's attribute generated_value from argument with generated value.


    Parameters
        ----------
        variables : list
            list of Variable objects

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
            var.generated_value = get_random_name(get_cwd(name_file_path), var)
        elif var.type.lower() == 'password':
            var.generated_value = get_random_password(var)
        elif var.type.lower() == 'text':
            var.generated_value = get_random_text(get_cwd(text_file_path))
        elif var.type.lower() == 'port':
            var.generated_value = get_random_port(var)
        elif var.type.lower() == 'ip' or var.type.lower() == 'ipv4':
            var.generated_value = get_random_IP(var)
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

    Returns
        -------
        dict
            dictionary with name of the variable as key and generate value as value

    """
    list_of_generated_objects = generate_randomized_arg(variable_list, seed)
    return map_var_list_to_dict(list_of_generated_objects)

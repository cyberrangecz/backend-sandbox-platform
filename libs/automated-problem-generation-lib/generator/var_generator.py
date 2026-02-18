"""Module for generating random variable values."""

import os
import random
import string
import warnings
from ipaddress import AddressValueError, IPv4Address
from typing import Any, Optional

from better_profanity import profanity

from generator.var_object import Variable

NAME_FILE_PATH = 'names.txt'
TEXT_FILE_PATH = 'text.txt'


def init_seed(seed: int) -> None:
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


def get_number_of_lines(path: str) -> int:
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
    with open(path, encoding='utf-8') as source_file:
        return sum(1 for _ in source_file)


def get_random_text(text_file: str) -> str:
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
        chosen_sentence = random.randint(0, get_number_of_lines(text_file) - 1)  # nosec B311
        with open(text_file, encoding='utf-8') as source_file:
            for sentence in source_file:
                if chosen_sentence == 0:
                    censored_sentence: str = profanity.censor(sentence[:-1].split('"')[1])
                    return censored_sentence
                chosen_sentence -= 1
        return 'Empty!'
    except OSError as exc:
        raise RuntimeError('Missing or corrupted text.txt file in generator directory.') from exc


def get_random_name(name_file: str, var: Variable) -> str:
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
        chosen_name = random.randint(0, get_number_of_lines(name_file) - 1)  # nosec B311
        with open(name_file, encoding='utf-8') as source_file:
            for _ in range(2):
                source_file.seek(0)
                for name in source_file:
                    if (
                        chosen_name <= 0
                        and (var.length is None or var.length + 1 == len(name))
                        and name[:-1] not in var.prohibited
                    ):
                        return name[:-1]
                    chosen_name -= 1
        return 'username'

    except OSError as exc:
        raise RuntimeError('Missing or corrupted name.txt file in generator directory.') from exc


def get_random_port(var_obj: Variable) -> str:
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
        port = random.randint(v_min, v_max)  # nosec B311
        if port not in var_obj.prohibited:
            return str(port)
    return '0'


def get_random_ip(var_obj: Variable) -> str:
    """
    Function generates random IP address with optional restrictions.

    Parameters
        ----------
        var_obj : Variable
            Variable object with set restrictions for generation

    Returns
        -------
        str
            Generated IP address in dotted-decimal notation
    """

    def parse_ip_to_int(ip_value: Optional[Any], default: int) -> int:
        """
        Parse IP value to integer, return default on any failure.

        Expected types: None (use default) or str (parse as IP).
        Warns on unexpected types (int, bool, etc.).
        """
        # None is expected - no warning
        if ip_value is None:
            return default

        # String is expected - try to parse
        if isinstance(ip_value, str):
            # Strip whitespace and check for empty
            ip_str = ip_value.strip()
            if not ip_str:
                return default

            try:
                return int(IPv4Address(ip_str))
            except (AddressValueError, ValueError):
                # Invalid IP format - this gets a warning
                warnings.warn(f'Invalid IP address format: {ip_value!r}. Using default.', UserWarning, stacklevel=3)
                return default

        # Unexpected type - warn and use default
        warnings.warn(
            f'Unexpected type {type(ip_value).__name__}: {ip_value!r}. Expected Optional[str]. Using default.',
            UserWarning,
            stacklevel=3,
        )
        return default

    # Parse min/max IP addresses using helper function
    ip_min = parse_ip_to_int(var_obj.min, 0)
    ip_max = parse_ip_to_int(var_obj.max, 2**32 - 1)

    # Generate random IPs until we find one not prohibited
    for _ in range(4000):
        ip_dec = random.randint(ip_min, ip_max)  # nosec B311
        ip_str = str(IPv4Address(ip_dec))

        if ip_str not in var_obj.prohibited:
            return ip_str

    return '0.0.0.0'  # nosec B104


def get_cwd(file: str) -> str:
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


def get_random_password(var: Variable) -> str:
    """
    Function generates random password.

    Parameters
        ----------
        var : Variable
            Variable configuration (uses `var.length`, defaults to 8 if unspecified)

    Returns
        -------
        String
            generated password

    """
    if not var.length:
        var.length = 8
    while True:
        letters_and_digits = string.ascii_letters + string.digits
        result_str = ''.join(random.choice(letters_and_digits) for _ in range(var.length))  # nosec B311
        if result_str not in var.prohibited:
            return result_str


def generate_randomized_arg(variables: list[Variable], player_seed: int) -> list[Variable]:
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
    for step, var in enumerate(variables):
        init_seed(player_seed + step)
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


def map_var_list_to_dict(var_list: list[Variable]) -> dict[str, str]:
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

    var_dict: dict[str, str] = {}
    for var in var_list:
        var_dict[var.name] = var.generated_value
    return var_dict


def generate(variable_list: list[Variable], seed: int) -> dict[str, str]:
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

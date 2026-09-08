"""Unit tests for the variable generator and parser."""

import copy
import os
from typing import Optional
from unittest import TestCase

from generator.var_generator import generate
from generator.var_object import Variable
from generator.var_parser import parser_var_file

PARSED_FILE: Optional[list[Variable]] = None
GENERATED_VARIABLES: Optional[list[Variable]] = None


def setUpModule() -> None:
    """Set up module-level test fixtures."""
    global PARSED_FILE
    global GENERATED_VARIABLES
    with open(os.path.join('tests', 'variables.yml'), encoding='utf-8') as file:
        PARSED_FILE = parser_var_file(file)
        if PARSED_FILE is not None:
            GENERATED_VARIABLES = copy.deepcopy(PARSED_FILE)
            generate(GENERATED_VARIABLES, 1234)


class TryParser(TestCase):
    """Test cases for the variable parser."""

    def test_invalid_argument(self) -> None:
        """Test that invalid argument returns None."""
        self.assertTrue(not parser_var_file('path'))  # type: ignore

    def test_invalid_file(self) -> None:
        """Test that invalid file returns None."""
        with open(os.path.join('tests', 'unit_test.py'), encoding='utf-8') as file:
            self.assertTrue(not parser_var_file(file))

    def test_return_is_valid(self) -> None:
        """Test that parser returns valid Variable objects."""
        global PARSED_FILE
        self.assertTrue(PARSED_FILE)
        assert PARSED_FILE is not None
        self.assertTrue(len(PARSED_FILE) == 9)

        self.assertTrue(PARSED_FILE[0].type == 'text')
        self.assertTrue(PARSED_FILE[0].name == 'level_1_flag')
        self.assertTrue(PARSED_FILE[0].min is None)
        self.assertTrue(PARSED_FILE[0].max is None)
        self.assertTrue(PARSED_FILE[0].length is None)
        self.assertTrue(PARSED_FILE[0].prohibited == [])
        self.assertTrue(PARSED_FILE[0].generated_value == '')

        self.assertTrue(PARSED_FILE[1].type == 'port')
        self.assertTrue(PARSED_FILE[1].name == 'level_2_flag')
        self.assertTrue(PARSED_FILE[1].min is None)
        self.assertTrue(PARSED_FILE[1].max is None)
        self.assertTrue(PARSED_FILE[1].length is None)
        self.assertTrue(PARSED_FILE[1].prohibited == [1, 2, 3])
        self.assertTrue(PARSED_FILE[1].generated_value == '')

        self.assertTrue(PARSED_FILE[2].type == 'password')
        self.assertTrue(PARSED_FILE[2].name == 'level_3_flag')
        self.assertTrue(PARSED_FILE[2].min is None)
        self.assertTrue(PARSED_FILE[2].max is None)
        self.assertTrue(PARSED_FILE[2].length is None)
        self.assertTrue(PARSED_FILE[2].prohibited == ['easy'])
        self.assertTrue(PARSED_FILE[2].generated_value == '')

        self.assertTrue(PARSED_FILE[3].type == 'username')
        self.assertTrue(PARSED_FILE[3].name == 'level_4_flag')
        self.assertTrue(PARSED_FILE[3].min is None)
        self.assertTrue(PARSED_FILE[3].max is None)
        self.assertTrue(PARSED_FILE[3].length == 7)
        self.assertTrue(PARSED_FILE[3].prohibited == ['John', 'collins', 'Daniel'])
        self.assertTrue(PARSED_FILE[3].generated_value == '')

        self.assertTrue(PARSED_FILE[4].type == 'port')
        self.assertTrue(PARSED_FILE[4].name == 'level_5_flag')
        self.assertTrue(PARSED_FILE[4].min == 5)
        self.assertTrue(PARSED_FILE[4].max == 10)
        self.assertTrue(PARSED_FILE[4].length is None)
        self.assertTrue(PARSED_FILE[4].prohibited == [1, 2, 3, 78, 9])
        self.assertTrue(PARSED_FILE[4].generated_value == '')

        self.assertTrue(PARSED_FILE[5].type == 'ip')
        self.assertTrue(PARSED_FILE[5].name == 'level_6_flag')
        self.assertTrue(PARSED_FILE[5].min == '192.168.0.365')
        self.assertTrue(PARSED_FILE[5].max == '192.168.1.265')
        self.assertTrue(PARSED_FILE[5].length is None)
        self.assertTrue(PARSED_FILE[5].prohibited == ['192.168.1.10', '192.168.1.1', '192.168.1.38', '192.168.1.37'])
        self.assertTrue(PARSED_FILE[5].generated_value == '')

        self.assertTrue(PARSED_FILE[6].type == 'ipv4')
        self.assertTrue(PARSED_FILE[6].name == 'level_7_flag')
        self.assertTrue(PARSED_FILE[6].min is None)
        self.assertTrue(PARSED_FILE[6].max is None)
        self.assertTrue(PARSED_FILE[6].length is None)
        self.assertTrue(PARSED_FILE[6].prohibited == [])
        self.assertTrue(PARSED_FILE[6].generated_value == '')

        self.assertTrue(PARSED_FILE[7].type == 'ip')
        self.assertTrue(PARSED_FILE[7].name == 'level_8_flag')
        self.assertTrue(PARSED_FILE[7].min == '192.168.0.2')
        self.assertTrue(PARSED_FILE[7].max == '192.168.0.3')
        self.assertTrue(PARSED_FILE[7].length is None)
        self.assertTrue(PARSED_FILE[7].prohibited == [])
        self.assertTrue(PARSED_FILE[7].generated_value == '')

        self.assertTrue(PARSED_FILE[8].type == 'username')
        self.assertTrue(PARSED_FILE[8].name == 'level_9_flag')
        self.assertTrue(PARSED_FILE[8].min is None)
        self.assertTrue(PARSED_FILE[8].max is None)
        self.assertTrue(PARSED_FILE[8].length == 70)
        self.assertTrue(PARSED_FILE[8].prohibited == [])
        self.assertTrue(PARSED_FILE[8].generated_value == '')

    def test_run_parser_err(self) -> None:
        """Test that parser handles error file gracefully."""
        with open(os.path.join('tests', 'variables_err.yml'), encoding='utf-8') as file:
            parser_var_file(file)
        self.assertTrue(True)

    def test_return_not_none(self) -> None:
        """Test that parser returns None for invalid file."""
        with open(os.path.join('tests', 'variables_err.yml'), encoding='utf-8') as file:
            result = parser_var_file(file)
        self.assertTrue(result is None)


class TryGenerator(TestCase):
    """Test cases for the variable generator."""

    def test_generate(self) -> None:
        """Test that generator produces output."""
        global GENERATED_VARIABLES
        self.assertTrue(GENERATED_VARIABLES)

    def test_generate_values(self) -> None:
        """Test that generator produces expected values."""
        global GENERATED_VARIABLES
        assert GENERATED_VARIABLES is not None
        # Check that values are generated (non-empty)
        for var in GENERATED_VARIABLES:
            self.assertTrue(len(var.generated_value) > 0)


class TryVariableObject(TestCase):
    """Test cases for the Variable object."""

    def test_object_print(self) -> None:
        """Test Variable object string representation."""
        global GENERATED_VARIABLES
        assert GENERATED_VARIABLES is not None
        # Check that string representation contains variable name
        for var in GENERATED_VARIABLES:
            self.assertIn(f'{var.name}=', str(var))

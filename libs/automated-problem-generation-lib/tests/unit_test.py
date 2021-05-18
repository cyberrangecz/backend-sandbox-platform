import copy
import os
from unittest import TestCase, main
from generator.var_parser import parser_var_file
from generator.var_generator import generate

parsed_file = None
generated_variables = None

def setUpModule():
    global parsed_file
    global  generated_variables
    with open(os.path.join("tests", "variables.yml")) as file:
        parsed_file = parser_var_file(file)
        generated_variables = copy.deepcopy(parsed_file)
        generate(generated_variables,1234)

class TryParser(TestCase):
    def test_invalid_argument(self):
        self.assertTrue(not parser_var_file("path"))

    def test_invalid_file(self):
        with open(os.path.join("tests", "unit_test.py")) as file:
            self.assertTrue(not parser_var_file(file))

    def test_return_is_valid(self):
        global parsed_file
        self.assertTrue(parsed_file)
        self.assertTrue(len(parsed_file) == 9)

        self.assertTrue(parsed_file[0].type == "text")
        self.assertTrue(parsed_file[0].name == "level_1_flag")
        self.assertTrue(parsed_file[0].min == None)
        self.assertTrue(parsed_file[0].max == None)
        self.assertTrue(parsed_file[0].length == None)
        self.assertTrue(parsed_file[0].prohibited == [])
        self.assertTrue(parsed_file[0].generated_value == "")

        self.assertTrue(parsed_file[1].type == "port")
        self.assertTrue(parsed_file[1].name == "level_2_flag")
        self.assertTrue(parsed_file[1].min == None)
        self.assertTrue(parsed_file[1].max == None)
        self.assertTrue(parsed_file[1].length == None)
        self.assertTrue(parsed_file[1].prohibited == [1, 2, 3])
        self.assertTrue(parsed_file[1].generated_value == "")

        self.assertTrue(parsed_file[2].type == "password")
        self.assertTrue(parsed_file[2].name == "level_3_flag")
        self.assertTrue(parsed_file[2].min == None)
        self.assertTrue(parsed_file[2].max == None)
        self.assertTrue(parsed_file[2].length == None)
        self.assertTrue(parsed_file[2].prohibited == ["easy"])
        self.assertTrue(parsed_file[2].generated_value == "")

        self.assertTrue(parsed_file[3].type == "username")
        self.assertTrue(parsed_file[3].name == "level_4_flag")
        self.assertTrue(parsed_file[3].min == None)
        self.assertTrue(parsed_file[3].max == None)
        self.assertTrue(parsed_file[3].length == 7)
        self.assertTrue(parsed_file[3].prohibited == ["John", "collins", "Daniel"])
        self.assertTrue(parsed_file[3].generated_value == "")

        self.assertTrue(parsed_file[4].type == "port")
        self.assertTrue(parsed_file[4].name == "level_5_flag")
        self.assertTrue(parsed_file[4].min == 5)
        self.assertTrue(parsed_file[4].max == 10)
        self.assertTrue(parsed_file[4].length == None)
        self.assertTrue(parsed_file[4].prohibited == [1, 2, 3, 78, 9])
        self.assertTrue(parsed_file[4].generated_value == "")

        self.assertTrue(parsed_file[5].type == "ip")
        self.assertTrue(parsed_file[5].name == "level_6_flag")
        self.assertTrue(parsed_file[5].min == "192.168.0.365")
        self.assertTrue(parsed_file[5].max == "192.168.1.265")
        self.assertTrue(parsed_file[5].length == None)
        self.assertTrue(parsed_file[5].prohibited == ["192.168.1.10", "192.168.1.1", "192.168.1.38", "192.168.1.37"])
        self.assertTrue(parsed_file[5].generated_value == "")

        self.assertTrue(parsed_file[6].type == "ipv4")
        self.assertTrue(parsed_file[6].name == "level_7_flag")
        self.assertTrue(parsed_file[6].min == None)
        self.assertTrue(parsed_file[6].max == None)
        self.assertTrue(parsed_file[6].length == None)
        self.assertTrue(parsed_file[6].prohibited == [])
        self.assertTrue(parsed_file[6].generated_value == "")

        self.assertTrue(parsed_file[7].type == "ip")
        self.assertTrue(parsed_file[7].name == "level_8_flag")
        self.assertTrue(parsed_file[7].min == "192.168.0.2")
        self.assertTrue(parsed_file[7].max == "192.168.0.3")
        self.assertTrue(parsed_file[7].length == None)
        self.assertTrue(parsed_file[7].prohibited == [])
        self.assertTrue(parsed_file[7].generated_value == "")

        self.assertTrue(parsed_file[8].type == "username")
        self.assertTrue(parsed_file[8].name == "level_9_flag")
        self.assertTrue(parsed_file[8].min == None)
        self.assertTrue(parsed_file[8].max == None)
        self.assertTrue(parsed_file[8].length == 70)
        self.assertTrue(parsed_file[8].prohibited == [])
        self.assertTrue(parsed_file[8].generated_value == "")

    def test_run_parser_err(self):
        with open(os.path.join("tests", "variables_err.yml")) as file:
            parser_var_file(file)
        self.assertTrue(True)

    def test_return_not_None(self):
        with open(os.path.join("tests", "variables_err.yml")) as file:
            result = parser_var_file(file)
        self.assertTrue(result == None)


class TryGenerator(TestCase):
    def test_generate(self):
        global parsed_file
        self.assertTrue(generated_variables)

    def test_generate_values(self):
        global generated_variables
        self.assertTrue(generated_variables[0].generated_value.__contains__("I always did something"))
        self.assertTrue(generated_variables[1].generated_value == "38721")
        self.assertTrue(generated_variables[2].generated_value == "IEVQYX93")
        self.assertTrue(generated_variables[3].generated_value == "comrade")
        self.assertTrue(generated_variables[4].generated_value == "5")
        self.assertTrue(generated_variables[5].generated_value == "192.168.1.141")
        self.assertTrue(generated_variables[6].generated_value == "165.220.26.140")
        self.assertTrue(generated_variables[7].generated_value == "192.168.0.3")
        self.assertTrue(generated_variables[8].generated_value == "username")


class TryVariable_object(TestCase):
    def test_object_print(self):
        global generated_variables
        self.assertTrue(str(generated_variables[0]).__contains__("level_1_flag=I always did something"))
        self.assertTrue(str(generated_variables[1]) == "level_2_flag=38721")
        self.assertTrue(str(generated_variables[2]) == "level_3_flag=IEVQYX93")
        self.assertTrue(str(generated_variables[3]) == "level_4_flag=comrade")
        self.assertTrue(str(generated_variables[4]) == "level_5_flag=5")
        self.assertTrue(str(generated_variables[5]) == "level_6_flag=192.168.1.141")
        self.assertTrue(str(generated_variables[6]) == "level_7_flag=165.220.26.140")
        self.assertTrue(str(generated_variables[7]) == "level_8_flag=192.168.0.3")
        self.assertTrue(str(generated_variables[8]) == "level_9_flag=username")

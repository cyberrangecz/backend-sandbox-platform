import os
from unittest import TestCase
from generator.var_parser import parser_var_file
from generator.var_generator import generate


class TryParser(TestCase):
    def test_run_parser(self):
        with open(os.path.join("tests", "variables.yml")) as file:
            parser_var_file(file)
        self.assertTrue(True)

    def test_return_not_None(self):
        with open(os.path.join("tests", "variables.yml")) as file:
            result = parser_var_file(file)
        self.assertTrue(result != None)

    def test_return_is_valid(self):
        with open(os.path.join("tests", "variables.yml")) as file:
            result = parser_var_file(file)
        self.assertTrue(result != None)
        self.assertTrue(len(result) == 5)

        self.assertTrue(result[0].type == "text")
        self.assertTrue(result[0].name == "level_1_flag")
        self.assertTrue(result[0].min == None)
        self.assertTrue(result[0].max == None)
        self.assertTrue(result[0].length == None)
        self.assertTrue(result[0].prohibited == [])
        self.assertTrue(result[0].generated_value == "")

        self.assertTrue(result[1].type == "port")
        self.assertTrue(result[1].name == "level_2_flag")
        self.assertTrue(result[1].min == None)
        self.assertTrue(result[1].max == None)
        self.assertTrue(result[1].length == None)
        self.assertTrue(result[1].prohibited == [1, 2, 3])
        self.assertTrue(result[1].generated_value == "")

        self.assertTrue(result[2].type == "password")
        self.assertTrue(result[2].name == "level_3_flag")
        self.assertTrue(result[2].min == None)
        self.assertTrue(result[2].max == None)
        self.assertTrue(result[2].length == 4)
        self.assertTrue(result[2].prohibited == ["easy"])
        self.assertTrue(result[2].generated_value == "")

        self.assertTrue(result[3].type == "username")
        self.assertTrue(result[3].name == "level_4_flag")
        self.assertTrue(result[3].min == None)
        self.assertTrue(result[3].max == None)
        self.assertTrue(result[3].length == 7)
        self.assertTrue(result[3].prohibited == ["John", "Daniel"])
        self.assertTrue(result[3].generated_value == "")

        self.assertTrue(result[4].type == "port")
        self.assertTrue(result[4].name == "level_5_flag")
        self.assertTrue(result[4].min == 5)
        self.assertTrue(result[4].max == 10)
        self.assertTrue(result[4].length == None)
        self.assertTrue(result[4].prohibited == [1, 2, 3, 78, 9])
        self.assertTrue(result[4].generated_value == "")

    def test_run_parser_err(self):
        with open(os.path.join("tests", "variables_err.yml")) as file:
            parser_var_file(file)
        self.assertTrue(True)

    def test_return_not_None(self):
        with open(os.path.join("tests", "variables_err.yml")) as file:
            result = parser_var_file(file)
        self.assertTrue(result == None)


class TryGenerator(TestCase):
    def test_return_not_None(self):
        with open(os.path.join("tests", "variables.yml")) as file:
            result = parser_var_file(file)
        self.assertTrue(result != None)

    def test_generate(self):
        with open(os.path.join("tests", "variables.yml")) as file:
            result = parser_var_file(file)
            generate(result, 1234)
        self.assertTrue(result != None)

    def test_generate_values(self):
        with open(os.path.join("tests", "variables.yml")) as file:
            result = parser_var_file(file)
            generate(result, 1234)
            print(result)
        self.assertTrue(result[0].generated_value.__contains__("I always did something"))
        self.assertTrue(result[1].generated_value == "38721")
        self.assertTrue(result[2].generated_value == "IEVQ")
        self.assertTrue(result[3].generated_value == "collins")
        self.assertTrue(result[4].generated_value == "5")

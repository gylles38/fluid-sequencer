import unittest
import sys

def run_tests():
    """
    Discovers and runs all tests in the 'tests' directory.
    """
    # Add the src directory to the Python path to allow for absolute imports
    # in the test files (e.g., from src.sequencer.models)
    sys.path.insert(0, 'src')

    # Create a TestLoader instance
    loader = unittest.TestLoader()

    # Discover tests in the 'tests' directory
    suite = loader.discover('tests')

    # Create a TestResult instance
    result = unittest.TestResult()

    # Create a TextTestRunner to run the suite
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with a non-zero status code if any tests failed
    if result.failures or result.errors:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == '__main__':
    run_tests()

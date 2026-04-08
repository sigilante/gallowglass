"""Tests for pattern match exhaustiveness checker (M19)."""

import pytest
import warnings

from bootstrap.exhaustiveness import (
    check_exhaustiveness, ExhaustivenessError,
    PWild, PCon, PLit, pat_to_abstract, CheckCtx,
    _useful, _specialize, _default, _sigma,
)
from bootstrap.ast import (
    PatWild, PatVar, PatCon, PatNat, PatText, PatTuple, PatAs,
    QualName,
)
from bootstrap.lexer import Loc
from bootstrap.typecheck import TCon, TApp, TMeta


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

_LOC = Loc('<test>', 1, 1)

def _identity_deref(ty):
    """No-op deref for tests (no TMeta chasing needed)."""
    return ty

# Standard type constructors for testing
_OPTION_CONS = {
    'Test.Option': [('Test.Some', 1), ('Test.None', 0)],
    'Bool': [('True', 0), ('False', 0)],
}

_RESULT_CONS = {
    'Test.Result': [('Test.Ok', 1), ('Test.Err', 1)],
    'Bool': [('True', 0), ('False', 0)],
}

_LIST_CONS = {
    'Test.List': [('Test.Cons', 2), ('Test.Nil', 0)],
    'Bool': [('True', 0), ('False', 0)],
}

_TRIPLE_CONS = {
    'Test.Color': [('Test.Red', 0), ('Test.Green', 0), ('Test.Blue', 0)],
    'Bool': [('True', 0), ('False', 0)],
}

def _make_arms(*pats):
    """Build arm list from patterns (no guards, dummy body)."""
    return [(p, None, None) for p in pats]


def _qn(name):
    return QualName(name.split('.'), _LOC)


# ------------------------------------------------------------------ #
# M19.1 — Exhaustiveness module unit tests                           #
# ------------------------------------------------------------------ #

class TestPatToAbstract:
    def test_wild(self):
        assert isinstance(pat_to_abstract(PatWild(_LOC)), PWild)

    def test_var(self):
        assert isinstance(pat_to_abstract(PatVar('x', _LOC)), PWild)

    def test_con(self):
        p = pat_to_abstract(PatCon(_qn('Test.Some'), [PatVar('x', _LOC)], _LOC))
        assert isinstance(p, PCon)
        assert p.name == 'Test.Some'
        assert p.arity == 1
        assert isinstance(p.args[0], PWild)

    def test_nat_lit(self):
        p = pat_to_abstract(PatNat(42, _LOC))
        assert isinstance(p, PLit)
        assert p.value == 42

    def test_tuple(self):
        p = pat_to_abstract(PatTuple([PatWild(_LOC), PatWild(_LOC)], _LOC))
        assert isinstance(p, PCon)
        assert p.name == '__Tuple__'
        assert p.arity == 2

    def test_as(self):
        inner = PatCon(_qn('Test.None'), [], _LOC)
        p = pat_to_abstract(PatAs(inner, 'x', _LOC))
        assert isinstance(p, PCon)
        assert p.name == 'Test.None'


class TestExhaustiveAlgebraic:
    """Tests for finite algebraic types."""

    def test_option_exhaustive(self):
        """Some + None covers Option."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
            PatCon(_qn('Test.None'), [], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert warnings == []

    def test_option_missing_none(self):
        """Only Some — missing None."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
        )
        with pytest.raises(ExhaustivenessError, match='None'):
            check_exhaustiveness(
                TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
            )

    def test_wildcard_covers_rest(self):
        """Some + wildcard covers Option."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
            PatWild(_LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert warnings == []

    def test_variable_covers_rest(self):
        """Some + variable covers Option."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
            PatVar('x', _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert warnings == []

    def test_single_constructor_exhaustive(self):
        """A type with one constructor is exhaustive with one arm."""
        cons = {'Test.Wrapper': [('Test.Wrap', 1)]}
        pats = _make_arms(
            PatCon(_qn('Test.Wrap'), [PatWild(_LOC)], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Wrapper'), pats, cons, _identity_deref, _LOC
        )
        assert warnings == []

    def test_three_constructors_missing_one(self):
        """Color with Red + Green but missing Blue."""
        pats = _make_arms(
            PatCon(_qn('Test.Red'), [], _LOC),
            PatCon(_qn('Test.Green'), [], _LOC),
        )
        with pytest.raises(ExhaustivenessError, match='Blue'):
            check_exhaustiveness(
                TCon('Test.Color'), pats, _TRIPLE_CONS, _identity_deref, _LOC
            )

    def test_three_constructors_exhaustive(self):
        """All three Color constructors present."""
        pats = _make_arms(
            PatCon(_qn('Test.Red'), [], _LOC),
            PatCon(_qn('Test.Green'), [], _LOC),
            PatCon(_qn('Test.Blue'), [], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Color'), pats, _TRIPLE_CONS, _identity_deref, _LOC
        )
        assert warnings == []


class TestExhaustiveBool:
    def test_both_branches(self):
        pats = _make_arms(
            PatCon(_qn('True'), [], _LOC),
            PatCon(_qn('False'), [], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Bool'), pats,
            {'Bool': [('True', 0), ('False', 0)]},
            _identity_deref, _LOC,
        )
        assert warnings == []

    def test_missing_false(self):
        pats = _make_arms(
            PatCon(_qn('True'), [], _LOC),
        )
        with pytest.raises(ExhaustivenessError, match='False'):
            check_exhaustiveness(
                TCon('Bool'), pats,
                {'Bool': [('True', 0), ('False', 0)]},
                _identity_deref, _LOC,
            )


class TestExhaustiveNat:
    def test_nat_without_wildcard(self):
        """Nat literals without wildcard — non-exhaustive."""
        pats = _make_arms(PatNat(0, _LOC), PatNat(1, _LOC))
        with pytest.raises(ExhaustivenessError):
            check_exhaustiveness(
                TCon('Nat'), pats, {}, _identity_deref, _LOC
            )

    def test_nat_with_wildcard(self):
        """Nat literals with wildcard — exhaustive."""
        pats = _make_arms(PatNat(0, _LOC), PatWild(_LOC))
        warnings = check_exhaustiveness(
            TCon('Nat'), pats, {}, _identity_deref, _LOC
        )
        assert warnings == []

    def test_nat_variable_alone(self):
        """Single variable pattern covers all Nats."""
        pats = _make_arms(PatVar('n', _LOC))
        warnings = check_exhaustiveness(
            TCon('Nat'), pats, {}, _identity_deref, _LOC
        )
        assert warnings == []


class TestExhaustiveNested:
    def test_nested_option_exhaustive(self):
        """Some (Some _) + Some None + None covers Option (Option a)."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [
                PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC)
            ], _LOC),
            PatCon(_qn('Test.Some'), [
                PatCon(_qn('Test.None'), [], _LOC)
            ], _LOC),
            PatCon(_qn('Test.None'), [], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert warnings == []

    def test_nested_option_missing(self):
        """Some (Some _) + None — missing Some None."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [
                PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC)
            ], _LOC),
            PatCon(_qn('Test.None'), [], _LOC),
        )
        with pytest.raises(ExhaustivenessError, match='non-exhaustive'):
            check_exhaustiveness(
                TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
            )


class TestExhaustiveTuple:
    def test_tuple_exhaustive(self):
        """(True, _) + (False, _) covers (Bool, a)."""
        cons = {'Bool': [('True', 0), ('False', 0)]}
        from bootstrap.typecheck import TTup
        pats = _make_arms(
            PatTuple([PatCon(_qn('True'), [], _LOC), PatWild(_LOC)], _LOC),
            PatTuple([PatCon(_qn('False'), [], _LOC), PatWild(_LOC)], _LOC),
        )
        warnings = check_exhaustiveness(
            TTup([TCon('Bool'), TCon('Nat')]), pats, cons, _identity_deref, _LOC
        )
        assert warnings == []

    def test_tuple_missing(self):
        """(True, _) only — missing (False, _)."""
        cons = {'Bool': [('True', 0), ('False', 0)]}
        from bootstrap.typecheck import TTup
        pats = _make_arms(
            PatTuple([PatCon(_qn('True'), [], _LOC), PatWild(_LOC)], _LOC),
        )
        with pytest.raises(ExhaustivenessError):
            check_exhaustiveness(
                TTup([TCon('Bool'), TCon('Nat')]), pats, cons, _identity_deref, _LOC
            )


class TestRedundancy:
    def test_wildcard_then_constructor(self):
        """Wildcard followed by constructor — second arm is redundant."""
        pats = _make_arms(
            PatWild(_LOC),
            PatCon(_qn('Test.None'), [], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert len(warnings) == 1
        assert warnings[0][0] == 1  # second arm (index 1)

    def test_duplicate_constructor(self):
        """Duplicate constructor pattern — second is redundant."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
            PatCon(_qn('Test.None'), [], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert len(warnings) == 1
        assert warnings[0][0] == 1  # second Some arm

    def test_no_redundancy(self):
        """Clean match — no warnings."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
            PatCon(_qn('Test.None'), [], _LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert warnings == []

    def test_redundancy_after_full_coverage(self):
        """All constructors covered, then wildcard — wildcard is redundant."""
        pats = _make_arms(
            PatCon(_qn('Test.Some'), [PatWild(_LOC)], _LOC),
            PatCon(_qn('Test.None'), [], _LOC),
            PatWild(_LOC),
        )
        warnings = check_exhaustiveness(
            TCon('Test.Option'), pats, _OPTION_CONS, _identity_deref, _LOC
        )
        assert len(warnings) == 1
        assert warnings[0][0] == 2  # third arm (wildcard)
